package org.xrbench.qualification;

import android.app.NativeActivity;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.Log;
import android.view.WindowManager;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

/** Local maintainer runner: records actual inference and OpenXR frame evidence. */
public final class QualificationActivity extends NativeActivity {
    static { System.loadLibrary("xrgpu_qualification"); }
    private volatile boolean cancelled;
    private static volatile QualificationActivity current;
    private static native void begin0();
    private static native String run0(String jobPath);
    private static native void cancel0();
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        current = this;
        begin0();
        String id = getIntent().getStringExtra("run_id");
        if (id == null || !id.matches("[a-zA-Z0-9_-]{1,100}")) { finish(); return; }
        new Thread(() -> execute(id), "xrbench-qualification").start();
    }
    private void execute(String id) {
        File destination = new File(getFilesDir(), "runs/" + id + ".json");
        JSONObject result;
        try {
            File source = new File(getFilesDir(), "jobs/" + id + ".json");
            if (source.length() > 16 * 1024 * 1024) throw new IllegalStateException("Job too large");
            if (cancelled) throw new IllegalStateException("Activity paused before qualification");
            result = new JSONObject(run0(source.getAbsolutePath()));
        } catch (Exception error) {
            Log.e("xrBench", "Qualification failed", error);
            result = new JSONObject();
            try { result.put("run_id", id).put("status", "failed").put("error", error.toString()); }
            catch (Exception ignored) { }
        }
        try {
            // execute_file atomically preserves its original terminal bytes.
            // Re-serializing through JSONObject would change that evidence.
            if (nativeEvidenceExists(destination, id)) {
                runOnUiThread(this::finish);
                return;
            }
            File parent = destination.getParentFile();
            if (!parent.isDirectory() && !parent.mkdirs()) throw new IllegalStateException("Cannot create evidence directory");
            File temporary = new File(parent, id + ".part");
            try (FileOutputStream output = new FileOutputStream(temporary)) {
                output.write(result.toString().getBytes(StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            if (!temporary.renameTo(destination)) throw new IllegalStateException("Cannot preserve evidence");
        } catch (Exception error) { Log.e("xrBench", "Cannot preserve qualification evidence", error); }
        runOnUiThread(this::finish);
    }
    private static boolean nativeEvidenceExists(File file, String id) throws Exception {
        if (!file.isFile() || file.length() > 64L * 1024 * 1024) return false;
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
        }
        JSONObject report = new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        String status = report.optString("status");
        return id.equals(report.optString("run_id")) && (status.equals("complete") || status.equals("failed"));
    }
    /** Called around measurement blocks, outside the inference timer. */
    public static String conditions0() throws Exception {
        QualificationActivity activity = current;
        if (activity == null || activity.cancelled) throw new IllegalStateException("Qualification activity paused");
        PowerManager power = (PowerManager)activity.getSystemService(POWER_SERVICE);
        Intent battery = activity.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (power == null || battery == null || !battery.hasExtra(BatteryManager.EXTRA_PLUGGED))
            throw new IllegalStateException("Thermal or charging status unavailable");
        return new JSONObject().put("thermal_status", power.getCurrentThermalStatus())
                .put("charging", battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1) > 0).toString();
    }
    @Override protected void onPause() { cancelled = true; cancel0(); super.onPause(); }
    @Override protected void onDestroy() {
        cancelled = true; cancel0();
        if (current == this) current = null;
        super.onDestroy();
    }
}
