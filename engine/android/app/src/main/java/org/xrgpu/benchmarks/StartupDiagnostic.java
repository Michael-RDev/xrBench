package org.xrgpu.benchmarks;

import java.io.File;
import org.json.JSONObject;
import android.util.Log;

/** Every accepted browser request must receive evidence or an explicit rejection. */
final class StartupDiagnostic {
    static void write(File root, String probe, String run, String code, String message) {
        try {
            if (probe != null && probe.matches("probe-[0-9a-f]{32}")) {
                Fileset.write(new File(root, "profiles/" + probe + ".json"),
                    new JSONObject().put("probe_id", probe).put("code", code).put("error", message));
            } else if (run != null && run.matches("run-[0-9a-f]{32}")) {
                Fileset.write(new File(root, "runs/" + run + "/startup-error.json"),
                    new JSONObject().put("run_id", run).put("code", code).put("error", message));
            }
        } catch (Exception error) { Log.e("xrBench", "Cannot preserve startup diagnostic", error); }
    }
    static final String BUSY = "Another benchmark is still running on this headset. Stop it from the headset's xrBench notification, or return to its original browser tab, before starting a new run. Existing reports are preserved.";
}
