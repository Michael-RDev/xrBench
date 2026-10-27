"""Exercise the real service with JVM Android doubles; not physical OS evidence."""

import os
import shutil
import subprocess

import pytest

from engine.paths import ENGINE, ROOT


def test_service_completion_failure_cancel_deadline_and_destruction(tmp_path):
    jar = ROOT / "artifacts/test-deps/json.jar"
    if not jar.exists() or not shutil.which("javac"):
        pytest.skip("JDK and repository JSON test jar required")
    sources = {
        "android/app/Service.java": """
package android.app;
import java.io.File;import java.util.*;import android.content.Intent;import android.os.IBinder;
public class Service {
 public static final int START_NOT_STICKY=2,STOP_FOREGROUND_REMOVE=1;
 public static File root;public static Map<Class<?>,Object> services=new HashMap<>();
 public int removed,stopped;
 public <T>T getSystemService(Class<T> type){return type.cast(services.get(type));}
 public File getFilesDir(){return root;}public void onCreate(){}public void onDestroy(){}
 public int onStartCommand(Intent i,int f,int id){return 0;}public IBinder onBind(Intent i){return null;}
 public void startForeground(int id,Notification n,int type){}
 public void stopForeground(int flags){removed++;}public void stopSelf(){stopped++;}
 public void stopSelf(int id){stopped++;}
}""",
        "android/content/Intent.java": """
package android.content;import java.util.*;
public class Intent {String action;Map<String,String> values=new HashMap<>();
 public Intent(){}public Intent(Object o,Class<?> c){}
 public Intent setAction(String a){action=a;return this;}public String getAction(){return action;}
 public Intent putExtra(String k,String v){values.put(k,v);return this;}
 public String getStringExtra(String k){return values.get(k);}}
""",
        "android/app/Notification.java": """
package android.app;
public class Notification {public static final String CATEGORY_SERVICE="service";
 public static class Builder {
  public Builder(Object o,String c){}public Builder setSmallIcon(int i){return this;}
  public Builder setContentTitle(String s){return this;}public Builder setContentText(String s){return this;}
  public Builder setOngoing(boolean b){return this;}public Builder setOnlyAlertOnce(boolean b){return this;}
  public Builder setCategory(String s){return this;}public Builder addAction(Action a){return this;}
  public Notification build(){return new Notification();}}
 public static class Action {public static class Builder {
  public Builder(Object o,String s,PendingIntent p){}public Action build(){return new Action();}}}}
""",
        "android/app/NotificationChannel.java": "package android.app;public class NotificationChannel {public NotificationChannel(String a,String b,int c){}}",
        "android/app/NotificationManager.java": "package android.app;public class NotificationManager {public static final int IMPORTANCE_LOW=2;public void createNotificationChannel(NotificationChannel c){}public void notify(int i,Notification n){}}",
        "android/app/PendingIntent.java": "package android.app;import android.content.Intent;public class PendingIntent {public static final int FLAG_UPDATE_CURRENT=1,FLAG_IMMUTABLE=2;public static PendingIntent getService(Object o,int i,Intent n,int f){return new PendingIntent();}}",
        "android/content/pm/ServiceInfo.java": "package android.content.pm;public class ServiceInfo {public static final int FOREGROUND_SERVICE_TYPE_SPECIAL_USE=1,FOREGROUND_SERVICE_TYPE_DATA_SYNC=2;}",
        "android/os/Build.java": "package android.os;public class Build {public static class VERSION {public static int SDK_INT=34;}}",
        "android/os/IBinder.java": "package android.os;public interface IBinder {}",
        "android/os/Looper.java": "package android.os;public class Looper {public static Looper getMainLooper(){return new Looper();}}",
        "android/os/Handler.java": """
package android.os;import java.util.*;public class Handler {
 public Map<Runnable,Long> delayed=new HashMap<>();public Handler(Looper l){}
 public void post(Runnable r){r.run();}public void postDelayed(Runnable r,long ms){delayed.put(r,ms);}
 public void removeCallbacks(Runnable r){delayed.remove(r);}}
""",
        "android/os/SystemClock.java": "package android.os;public class SystemClock {public static long elapsedRealtime(){return System.nanoTime()/1000000;}}",
        "android/os/BatteryManager.java": "package android.os;public class BatteryManager {public boolean isCharging(){return false;}}",
        "android/os/PowerManager.java": """
package android.os;public class PowerManager {
 public static final int THERMAL_STATUS_SEVERE=3,PARTIAL_WAKE_LOCK=1;public WakeLock wake=new WakeLock();
 public int getCurrentThermalStatus(){return 0;}public boolean isInteractive(){return false;}
 public WakeLock newWakeLock(int i,String tag){return wake;}
 public static class WakeLock {public boolean held;public int releases;public long timeout;
  public void setReferenceCounted(boolean b){}public void acquire(long ms){held=true;timeout=ms;}
  public boolean isHeld(){return held;}public void release(){held=false;releases++;}}}
""",
        "android/util/Log.java": "package android.util;public class Log {public static int e(String a,String b,Throwable e){return 0;}}",
        "android/R.java": "package android;public class R {public static class drawable {public static final int stat_notify_sync=1;}}",
        "org/xrgpu/benchmarks/Doubles.java": """
package org.xrgpu.benchmarks;
import org.json.*;import java.io.*;import java.util.concurrent.*;import java.util.concurrent.atomic.*;import java.util.function.*;
class BuildConfig {static final boolean PUBLIC_NATIVE=true;}
class Checks {static void identity(String id){if(!id.matches("run-[0-9a-f]{32}"))throw new IllegalArgumentException();}}
class Fileset {
 static ConcurrentHashMap<String,JSONObject> saved=new ConcurrentHashMap<>();
 static JSONObject read(File f)throws Exception{return new JSONObject().put("mode","isolated");}
 static void write(File f,JSONObject j)throws Exception{saved.put(f.getName(),new JSONObject(j.toString()));}}
class NativeOperatorRunner {
 static Runnable action=()->{};
 static void run(Object o,String id){action.run();}
 static JSONObject profile(Object o){return new JSONObject();}}
class DeviceProfile {static JSONObject collect(Object o){return new JSONObject();}}
class Runner {static AtomicBoolean active=new AtomicBoolean();Runner(Object o,String s,AtomicBoolean b,Consumer<String> c){}void run(){}}
""",
        "org/xrgpu/benchmarks/ServiceChecks.java": """
package org.xrgpu.benchmarks;
import android.app.*;import android.os.*;import android.content.*;import java.io.*;import java.util.concurrent.*;import org.json.*;
public class ServiceChecks {
 static final String ID="run-"+"1".repeat(32);
 static void require(boolean b){if(!b)throw new AssertionError();}
 static BenchmarkService service(File root)throws Exception{
  Service.root=root;root.mkdirs();Fileset.saved.clear();
  Service.services.put(PowerManager.class,new PowerManager());
  Service.services.put(NotificationManager.class,new NotificationManager());
  Service.services.put(BatteryManager.class,new BatteryManager());
  BenchmarkService s=new BenchmarkService();s.onCreate();return s;}
 static boolean event(String name)throws Exception{
  JSONArray rows=Fileset.saved.get("lifecycle.json").getJSONArray("events");
  for(int i=0;i<rows.length();i++)if(name.equals(rows.getJSONObject(i).getString("event")))return true;return false;}
 static void clean(BenchmarkService s)throws Exception{
  s.worker.join(2000);require(!s.worker.isAlive());
  require(!s.wake.isHeld());require(((PowerManager.WakeLock)s.wake).releases==1);
  require(!s.main.delayed.containsKey(s.timeout)&&!s.main.delayed.containsKey(s.environment));
  require(s.removed==1);require(event("wake_released")&&event("service_finished"));
  s.finish(1);require(s.removed==1);s.onDestroy();require(event("service_destroyed"));}
 public static void main(String[] args)throws Exception{
  for(String mode:new String[]{"complete","failure","cancel","deadline","destroy"}){
   BenchmarkService s=service(new File(args[0],mode));
   CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
   NativeOperatorRunner.action=()->{
    entered.countDown();if(mode.equals("failure"))throw new IllegalStateException("native failed");
    if(!mode.equals("complete"))try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}};
   s.onStartCommand(new Intent().putExtra("run_id",ID),0,1);require(entered.await(2,TimeUnit.SECONDS));
   if(!mode.equals("complete")&&!mode.equals("failure")){
    require(s.main.delayed.get(s.timeout)==1800000L);require(s.wake.isHeld());
    if(mode.equals("deadline")){s.timeout.run();require(event("deadline_reached"));
     require(Fileset.saved.get("cancel-reason.json").getString("reason").contains("deadline"));}
    if(mode.equals("cancel")){s.onStartCommand(new Intent().setAction(BenchmarkService.CANCEL).putExtra("run_id",ID),0,2);
     require(s.cancelled.get());require(new File(Service.root,"runs/"+ID+"/cancel").exists());release.countDown();}
    if(mode.equals("destroy")){s.onDestroy();require(s.cancelled.get());}
   }
   clean(s);if(mode.equals("failure"))require(Fileset.saved.containsKey("startup-error.json"));
   if(mode.equals("cancel")||mode.equals("deadline")||mode.equals("destroy")){
    String expected=mode.equals("cancel")?"notification":mode.equals("deadline")?"deadline":"service_destroyed";
    JSONArray rows=Fileset.saved.get("lifecycle.json").getJSONArray("events");boolean found=false;
    for(int i=0;i<rows.length();i++)if(rows.getJSONObject(i).getString("event").equals("stop_requested"))
     found|=expected.equals(rows.getJSONObject(i).getJSONObject("details").getString("source"));
    require(found);
   }
  }
  System.out.println("service lifecycle checks passed");}
}
""",
    }
    files = []
    for relative, source in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        files.append(str(path))
    actual = ENGINE / "android/app/src/main/java/org/xrgpu/benchmarks/BenchmarkService.java"
    build = subprocess.run(["javac", "-cp", str(jar), "-d", str(tmp_path), *files, str(actual)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run(["java", "-cp", os.pathsep.join((str(tmp_path), str(jar))),
                          "org.xrgpu.benchmarks.ServiceChecks", str(tmp_path / "state")],
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert "service lifecycle checks passed" in run.stdout
