package com.edgehealth.relay

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.lifecycle.lifecycleScope
import com.edgehealth.relay.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: SharedPreferences

    private val hcPermissions = setOf(
        HealthPermission.getReadPermission(HeartRateRecord::class),
        HealthPermission.getReadPermission(OxygenSaturationRecord::class),
        HealthPermission.getReadPermission(StepsRecord::class),
    )

    private val notifPermLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op */ }

    private val hcPermLauncher =
        registerForActivityResult(PermissionController.createRequestPermissionResultContract()) {
                granted ->
            if (granted.containsAll(hcPermissions)) {
                Toast.makeText(this, "Health Connect permissions granted", Toast.LENGTH_SHORT)
                    .show()
                updateStatus()
            } else {
                Toast.makeText(this, "Permissions denied", Toast.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = getSharedPreferences("edgehealth", Context.MODE_PRIVATE)
        binding.piUrl.setText(prefs.getString("pi_url", "http://10.0.0.153:8000"))

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        binding.btnPerms.setOnClickListener { requestHcPermissions() }

        binding.btnStart.setOnClickListener {
            prefs.edit().putString("pi_url", binding.piUrl.text.toString().trim()).apply()
            startService(Intent(this, HealthRelayService::class.java))
            Toast.makeText(this, "Relay started", Toast.LENGTH_SHORT).show()
            updateStatus()
        }
        binding.btnStop.setOnClickListener {
            stopService(Intent(this, HealthRelayService::class.java))
            Toast.makeText(this, "Relay stopped", Toast.LENGTH_SHORT).show()
            updateStatus()
        }

        updateStatus()
    }

    private fun requestHcPermissions() {
        lifecycleScope.launch {
            try {
                val status = HealthConnectClient.getSdkStatus(this@MainActivity)
                if (status != HealthConnectClient.SDK_AVAILABLE) {
                    Toast.makeText(
                        this@MainActivity,
                        "Health Connect SDK not available (status=$status)",
                        Toast.LENGTH_LONG,
                    ).show()
                    return@launch
                }
                val client = HealthConnectClient.getOrCreate(this@MainActivity)
                val granted = client.permissionController.getGrantedPermissions()
                if (granted.containsAll(hcPermissions)) {
                    Toast.makeText(
                        this@MainActivity,
                        "Already granted",
                        Toast.LENGTH_SHORT,
                    ).show()
                    return@launch
                }
                hcPermLauncher.launch(hcPermissions)
            } catch (e: Exception) {
                Log.e("MainActivity", "perm error", e)
                Toast.makeText(this@MainActivity, "Error: ${e.message}", Toast.LENGTH_LONG)
                    .show()
            }
        }
    }

    private fun updateStatus() {
        binding.statusText.text = buildString {
            append("Pi URL: ${prefs.getString("pi_url", "?")}\n")
            append("Last sent: ${prefs.getLong("last_send_ms", 0L).let {
                if (it == 0L) "never" else java.util.Date(it).toString()
            }}\n")
            append("Sent count: ${prefs.getInt("sent_count", 0)}\n")
            append("Last error: ${prefs.getString("last_error", "none")}")
        }
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }
}
