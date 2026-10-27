package org.xrgpu.benchmarks;

import android.content.Context;
import android.os.Build;
import java.io.*;
import java.nio.file.Files;
import java.security.MessageDigest;
import org.json.*;

/** Verified byte fixtures and both typed native APIs; no model/CPU fallback. */
final class NativeOperatorRunner {
    static byte[] asset(Context context,String name) throws IOException {
        try(InputStream in=context.getAssets().open(name);ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] data=new byte[65536];int size;
            while((size=in.read(data))!=-1){out.write(data,0,size);if(out.size()>64*1024*1024)throw new IOException("Native asset exceeds limit");}
            return out.toByteArray();
        }
    }
    static String sha(byte[] data) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(data);StringBuilder result=new StringBuilder();
        for(byte value:digest)result.append(String.format(java.util.Locale.ROOT,"%02x",value&255));
        return result.toString();
    }
    static JSONObject metadata(Context context) throws Exception {
        return new JSONObject(new String(asset(context,"native-config.json"),java.nio.charset.StandardCharsets.UTF_8));
    }
    static JSONObject profile(Context context) throws Exception {
        JSONObject meta=metadata(context),nativeInfo;
        try{nativeInfo=new JSONObject(NativeOperators.probe());}
        catch(Exception|LinkageError error){nativeInfo=new JSONObject().put("available",false).put("reason","Native Vulkan unavailable: "+error);}
        boolean available=nativeInfo.optBoolean("available",false);
        return nativeInfo.put("backends",new JSONObject().put("xrgpu_vulkan",new JSONObject()
                .put("compatible",available).put("status",available?"compatible":"unsupported")
                .put("reason",nativeInfo.optString("reason",available?"":"Vulkan 1.1 is unavailable"))))
            .put("vulkan",available).put("gpu",nativeInfo.optString("device",""))
            .put("profile_version",1).put("runner_revision",meta.getString("runner_revision"))
            .put("installation_id",DeviceProfile.installationId(context))
            .put("runtime_revision",meta.getString("runtime_revision"))
            .put("native_package_sha256",meta.getString("native_package_sha256"))
            .put("model",Build.MODEL).put("manufacturer",Build.MANUFACTURER)
            .put("product",Build.PRODUCT).put("device",Build.DEVICE).put("os",Build.FINGERPRINT)
            .put("sdk",Build.VERSION.SDK_INT).put("abi",new JSONArray(Build.SUPPORTED_ABIS))
            .put("soc_model",Build.VERSION.SDK_INT>=31?Build.SOC_MODEL:JSONObject.NULL)
            .put("soc_manufacturer",Build.VERSION.SDK_INT>=31?Build.SOC_MANUFACTURER:JSONObject.NULL);
    }
    static File extract(Context context,JSONObject meta) throws Exception {
        byte[] bytes=asset(context,"native-operators/inventory.json");
        if(!sha(bytes).equals(meta.getString("assets_inventory_sha256")))throw new IOException("Native asset inventory checksum mismatch");
        JSONObject files=new JSONObject(new String(bytes,java.nio.charset.StandardCharsets.UTF_8)).getJSONObject("files");
        File root=new File(context.getFilesDir(),"native-assets/"+meta.getString("assets_inventory_sha256"));
        if(!root.exists()&&!root.mkdirs())throw new IOException("Cannot create native asset cache");
        for(java.util.Iterator<String> names=files.keys();names.hasNext();){String name=names.next();
            if(!name.matches("[A-Za-z0-9_./+-]+")||name.startsWith("/")||name.contains("//"))throw new IOException("Invalid native asset path");
            File file=root;
            for(String part:name.split("/")){
                if(part.equals(".")||part.equals("..")||part.isEmpty())throw new IOException("Native asset path escape");
                file=new File(file,part);if(Files.isSymbolicLink(file.toPath()))throw new IOException("Native asset links are forbidden");
            }
            String expected=files.getString(name);
            if(file.isFile()&&sha(Files.readAllBytes(file.toPath())).equals(expected))continue;
            byte[] data=asset(context,"native-operators/"+name);
            if(!sha(data).equals(expected))throw new IOException("Native asset checksum mismatch: "+name);
            if(!file.getParentFile().exists()&&!file.getParentFile().mkdirs())throw new IOException("Cannot create native asset directory");
            File part=new File(file.getPath()+".part");
            try(FileOutputStream stream=new FileOutputStream(part)){stream.write(data);stream.getFD().sync();}
            Files.move(part.toPath(),file.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING,java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        }
        return root;
    }
    static void run(Context context,String id) throws Exception {
        Checks.identity(id);
        if(!Runner.active.compareAndSet(false,true))throw new IllegalStateException("Another benchmark is active");
        try{
            JSONObject meta=metadata(context),config=Fileset.read(new File(context.getFilesDir(),"commands/"+id+".json"));
            if(!id.equals(config.getString("run_id"))||!"xrgpu_vulkan".equals(config.getString("backend"))||!"isolated".equals(config.getString("mode")))throw new IllegalArgumentException("Invalid native run configuration");
            for(String key:new String[]{"suite_revision","runner_revision","runtime_revision","native_package_sha256","native_catalog_sha256"})
                if(!meta.getString(key).equals(config.getString(key)))throw new IllegalArgumentException("Native release identity changed: "+key);
            try(InputStream input=new FileInputStream(context.getApplicationInfo().sourceDir)){
                MessageDigest digest=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[65536];int length;
                while((length=input.read(buffer))!=-1)digest.update(buffer,0,length);
                StringBuilder actual=new StringBuilder();for(byte value:digest.digest())actual.append(String.format(java.util.Locale.ROOT,"%02x",value&255));
                if(!actual.toString().equals(config.getString("apk_sha256")))throw new IllegalArgumentException("Installed native runner differs from the website release");
            }
            File root=extract(context,meta);JSONObject suite=Fileset.read(new File(root,"suite.json"));
            JSONArray expected=new JSONArray();for(int i=0;i<suite.getJSONArray("cases").length();i++)expected.put(suite.getJSONArray("cases").getJSONObject(i).getString("id"));
            if(!expected.toString().equals(config.getJSONArray("cases").toString())||config.getInt("warmups")!=20||config.getInt("samples")!=100)throw new IllegalArgumentException("Public benchmarks require the complete standard native suite");
            JSONObject device=profile(context);if(!device.getJSONObject("backends").getJSONObject("xrgpu_vulkan").getBoolean("compatible"))throw new IllegalStateException(device.optString("reason","Native Vulkan unavailable"));
            NativeOperators.run(root.getAbsolutePath(),config.toString(),new File(context.getFilesDir(),"runs/"+id).getAbsolutePath(),device.toString());
        }finally{Runner.active.set(false);}
    }
}
