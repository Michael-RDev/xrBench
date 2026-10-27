package org.xrgpu.benchmarks;
import android.content.Context;
import android.os.Build;
import org.json.*;
import java.util.UUID;

public final class DeviceProfile {
    public static JSONObject collect(Context context) throws Exception {
        return collect(context,true);
    }
    public static JSONObject collect(Context context,boolean inspectGpu) throws Exception {
        JSONObject caps=new JSONObject().put("backends",new JSONObject());
        try {caps=new JSONObject(NativeCPU.capabilities());}
        catch(Exception|LinkageError error){
            JSONObject unsupported=new JSONObject().put("compatible",false).put("status","unsupported").put("reason","CPU library unavailable: "+error);
            caps.getJSONObject("backends").put("executorch_cpu",unsupported).put("cpu_resident",unsupported);
        }
        if(inspectGpu)try {
            JSONObject gpu=new JSONObject(Native.capabilities());
            JSONObject backends=caps.getJSONObject("backends");
            for(java.util.Iterator<String> keys=gpu.getJSONObject("backends").keys();keys.hasNext();){String key=keys.next();backends.put(key,gpu.getJSONObject("backends").get(key));}
            for(java.util.Iterator<String> keys=gpu.keys();keys.hasNext();){String key=keys.next();if(!key.equals("backends"))caps.put(key,gpu.get(key));}
        }catch(Exception|LinkageError error){
            JSONObject unsupported=new JSONObject().put("compatible",false).put("status","unsupported").put("reason","Vulkan unavailable: "+error);
            caps.put("vulkan",false).getJSONObject("backends").put("executorch_vulkan",unsupported).put("vulkan_resident",unsupported);
        }
        String installation=installationId(context);
        return caps.put("model",Build.MODEL).put("manufacturer",Build.MANUFACTURER).put("os",Build.FINGERPRINT)
            .put("product",Build.PRODUCT).put("device",Build.DEVICE)
            .put("soc_model",Build.VERSION.SDK_INT>=31?Build.SOC_MODEL:JSONObject.NULL)
            .put("soc_manufacturer",Build.VERSION.SDK_INT>=31?Build.SOC_MANUFACTURER:JSONObject.NULL)
            .put("sdk",Build.VERSION.SDK_INT).put("abi",new JSONArray(Build.SUPPORTED_ABIS))
            .put("installation_id",installation).put("profile_version",1)
            .put("execution_target","unknown").put("target_provenance","Not supplied")
            .put("xr_scene",new JSONObject().put("id","quest3_cube_grid").put("eligible",Build.MODEL.equals("Quest 3")));
    }
    static synchronized String installationId(Context context) {
        android.content.SharedPreferences prefs=context.getSharedPreferences("device-profile",Context.MODE_PRIVATE);
        String installation=prefs.getString("id",null);
        if(installation==null){
            installation=UUID.randomUUID().toString();
            if(!prefs.edit().putString("id",installation).commit())throw new IllegalStateException("Cannot preserve installation identity");
        }
        UUID.fromString(installation);
        return installation;
    }
}
