package org.xrgpu.benchmarks;

import org.json.*;
import java.util.Iterator;

/** Cache validity is independent of execution; the cache itself is app-private per device. */
public final class CalibrationPolicy {
    public static final int VERSION=1;
    public static JSONObject identity(JSONObject caps,String os,String mode,int thermal,boolean charging) throws JSONException {
        return new JSONObject().put("version",VERSION).put("gpu",caps.opt("gpu")).put("driver",caps.opt("driver"))
            .put("os",os).put("precision","fp32").put("mode",mode).put("runner",caps.get("runner_revision"))
            .put("thermal_start",thermal).put("charging_start",charging);
    }
    public static boolean compatible(JSONObject profile,JSONObject identity) {
        try {
            if(!profile.optBoolean("available")||profile.getInt("version")!=VERSION)return false;
            JSONObject saved=new JSONObject(profile.getString("identity"));
            if(saved.length()!=identity.length())return false;
            for(Iterator<String> keys=identity.keys();keys.hasNext();){String key=keys.next();if(!identity.get(key).equals(saved.get(key)))return false;}
            for(String key:new String[]{"compute_gflops","memory_gbps","dispatch_ms"}){
                double rate=profile.getDouble(key);if(!Double.isFinite(rate)||rate<=0)return false;
            }
            return profile.getLong("scratch_bytes")>0&&profile.getLong("scratch_bytes")<=64L*1024*1024
                &&profile.getDouble("wall_ms")>=0&&profile.getDouble("wall_ms")<=15000;
        }catch(JSONException error){return false;}
    }
}
