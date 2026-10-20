package org.xrgpu.benchmarks;

import android.content.Context;
import org.json.*;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.Iterator;

public final class Fileset {
    public final File root;
    public final JSONObject suite;
    public Fileset(Context context) throws Exception {
        JSONObject manifest;
        try(InputStream in=context.getAssets().open("bundle/manifest.json")) { ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)bytes.write(b,0,n);manifest=new JSONObject(bytes.toString("UTF-8")); }
        root=new File(context.getFilesDir(),"bundle-"+manifest.getString("suite_revision"));
        root.mkdirs();JSONObject files=manifest.getJSONObject("files");
        for(Iterator<String> keys=files.keys();keys.hasNext();) {
            String name=keys.next();File target=new File(root,name);
            if(!target.getCanonicalPath().startsWith(root.getCanonicalPath()+File.separator))throw new IOException("Invalid bundle path");
            String hash=files.getString(name);
            if(target.isFile()&&digest(target).equals(hash))continue;
            target.getParentFile().mkdirs();File temp=new File(target.getPath()+".part");
            try(InputStream in=context.getAssets().open("bundle/"+name);OutputStream out=new FileOutputStream(temp)) { byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n); }
            if(!digest(temp).equals(hash))throw new IOException("Bundle checksum mismatch");
            Files.move(temp.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING);
        }
        suite=read(new File(root,"suite.json"));
    }
    public static String digest(File file) throws Exception {
        MessageDigest d=MessageDigest.getInstance("SHA-256");
        try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}
        StringBuilder out=new StringBuilder();for(byte b:d.digest())out.append(String.format("%02x",b&255));return out.toString();
    }
    public static JSONObject read(File file) throws Exception {return new JSONObject(new String(Files.readAllBytes(file.toPath()),java.nio.charset.StandardCharsets.UTF_8));}
    public static void write(File file,JSONObject value) throws Exception {
        file.getParentFile().mkdirs();File tmp=new File(file.getPath()+".part");
        try(FileOutputStream out=new FileOutputStream(tmp)){out.write(value.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));out.getFD().sync();}
        Files.move(tmp.toPath(),file.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
    }
    public float[] floats(String name) throws Exception {
        byte[] bytes=Files.readAllBytes(new File(root,name).toPath());
        if(bytes.length%4!=0)throw new IOException("Invalid FP32 fixture");
        float[] result=new float[bytes.length/4];ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(result);return result;
    }
}
