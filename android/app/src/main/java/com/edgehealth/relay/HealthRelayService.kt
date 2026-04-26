package com.edgehealth.relay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.concurrent.TimeUnit

class HealthRelayService : Service() {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private lateinit var client: HealthConnectClient
    private lateinit var http: OkHttpClient
    private var wakeLock: PowerManager.WakeLock? = null
    private var loopJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        client = HealthConnectClient.getOrCreate(this)
        http = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "EdgeHealth::Relay").apply {
            acquire(8 * 60 * 60 * 1000L) // 8h max
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIF_ID,
                buildNotification("Starting…"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH,
            )
        } else {
            startForeground(NOTIF_ID, buildNotification("Starting…"))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (loopJob == null) {
            loopJob = scope.launch { runLoop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        loopJob?.cancel()
        scope.cancel()
        wakeLock?.takeIf { it.isHeld }?.release()
        super.onDestroy()
    }

    private suspend fun runLoop() {
        val prefs = getSharedPreferences("edgehealth", Context.MODE_PRIVATE)
        // High-water-mark: only POST readings whose ts is > this Instant.
        // Persisted so a service restart doesn't replay old samples.
        var hwm: Instant = prefs.getLong("hwm_ms", 0L)
            .takeIf { it > 0L }
            ?.let { Instant.ofEpochMilli(it) }
            ?: Instant.now().minus(24, ChronoUnit.HOURS)
        Log.i(TAG, "runLoop: starting; hwm=$hwm")
        while (true) {
            try {
                val now = Instant.now()
                // Always query a wide window (last 24h) so sparse HR pushes
                // from Samsung Health are caught regardless of poll cadence.
                val windowStart = now.minus(24, ChronoUnit.HOURS)
                Log.i(TAG, "poll: query $windowStart -> $now (hwm=$hwm)")
                val all = collect(windowStart, now)
                val fresh = all.filter {
                    val tsStr = it.optString("ts", "")
                    val ts = try { Instant.parse(tsStr) } catch (e: Exception) { null }
                    ts != null && ts.isAfter(hwm)
                }
                Log.i(TAG, "poll: HC returned ${all.size}, ${fresh.size} fresh")
                if (fresh.isNotEmpty()) {
                    val ok = postToPi(fresh)
                    Log.i(TAG, "poll: postToPi ok=$ok")
                    if (ok) {
                        // Advance HWM to the max ts we just posted.
                        val maxTs = fresh.mapNotNull {
                            try { Instant.parse(it.getString("ts")) } catch (_: Exception) { null }
                        }.maxOrNull() ?: now
                        // Cap hwm at "now" so a future-dated daily Steps
                        // record (endTime = end-of-day) doesn't lock us out.
                        hwm = if (maxTs.isAfter(now)) now else maxTs
                        prefs.edit().putLong("hwm_ms", hwm.toEpochMilli()).apply()
                        recordSuccess(fresh.size)
                    }
                } else {
                    recordError("none (0 new readings since $hwm)")
                }
                updateNotification("Last poll ${java.text.SimpleDateFormat("HH:mm:ss")
                    .format(java.util.Date())} — ${fresh.size} new (HC=${all.size})")
            } catch (e: Exception) {
                Log.e(TAG, "loop error", e)
                recordError(e.message ?: "unknown")
                updateNotification("Error: ${e.message}")
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    private suspend fun collect(start: Instant, end: Instant): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        val window = TimeRangeFilter.between(start, end)

        // Heart rate samples (each record may contain a series of samples)
        try {
            val hrResp = client.readRecords(
                ReadRecordsRequest(
                    recordType = HeartRateRecord::class,
                    timeRangeFilter = window,
                ),
            )
            for (rec in hrResp.records) {
                for (sample in rec.samples) {
                    out += JSONObject().apply {
                        put("ts", sample.time.toString())
                        put("metric", "hr")
                        put("value", sample.beatsPerMinute.toDouble())
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "HR read: ${e.message}")
        }

        try {
            val o2Resp = client.readRecords(
                ReadRecordsRequest(
                    recordType = OxygenSaturationRecord::class,
                    timeRangeFilter = window,
                ),
            )
            for (rec in o2Resp.records) {
                out += JSONObject().apply {
                    put("ts", rec.time.toString())
                    put("metric", "spo2")
                    put("value", rec.percentage.value)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "SpO2 read: ${e.message}")
        }

        try {
            val stepsResp = client.readRecords(
                ReadRecordsRequest(
                    recordType = StepsRecord::class,
                    timeRangeFilter = window,
                ),
            )
            for (rec in stepsResp.records) {
                out += JSONObject().apply {
                    put("ts", rec.endTime.toString())
                    put("metric", "steps")
                    put("value", rec.count.toDouble())
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Steps read: ${e.message}")
        }

        return out
    }

    private fun postToPi(readings: List<JSONObject>): Boolean {
        val prefs = getSharedPreferences("edgehealth", Context.MODE_PRIVATE)
        val piUrl = prefs.getString("pi_url", "http://10.0.0.153:8000") ?: return false
        val payload = JSONObject().apply {
            put("source", "android")
            put("readings", JSONArray(readings))
        }
        val body = payload.toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("$piUrl/ingest").post(body).build()
        return try {
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    recordError("HTTP ${resp.code}")
                }
                resp.isSuccessful
            }
        } catch (e: Exception) {
            recordError(e.message ?: "post failed")
            false
        }
    }

    private fun recordSuccess(n: Int) {
        val prefs = getSharedPreferences("edgehealth", Context.MODE_PRIVATE)
        prefs.edit()
            .putLong("last_send_ms", System.currentTimeMillis())
            .putInt("sent_count", prefs.getInt("sent_count", 0) + n)
            .putString("last_error", "none")
            .apply()
    }

    private fun recordError(msg: String) {
        getSharedPreferences("edgehealth", Context.MODE_PRIVATE).edit()
            .putString("last_error", msg)
            .apply()
    }

    private fun buildNotification(text: String): Notification {
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "EdgeHealth Relay",
                NotificationManager.IMPORTANCE_LOW,
            )
            mgr.createNotificationChannel(ch)
        }
        val tap = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("EdgeHealth Relay running")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_recent_history)
            .setOngoing(true)
            .setContentIntent(tap)
            .build()
    }

    private fun updateNotification(text: String) {
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        mgr.notify(NOTIF_ID, buildNotification(text))
    }

    companion object {
        private const val TAG = "EdgeHealthRelay"
        private const val CHANNEL_ID = "edgehealth_relay"
        private const val NOTIF_ID = 1001
        private const val POLL_INTERVAL_MS = 60_000L
    }
}
