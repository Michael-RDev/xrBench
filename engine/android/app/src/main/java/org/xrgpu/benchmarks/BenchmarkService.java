package org.xrgpu.benchmarks;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.BatteryManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;
import java.io.File;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** Owns isolated compute independently of the headset's display/activity lifecycle. */
public final class BenchmarkService extends Service {
    static final String CHANNEL="xrbench-benchmark";
    static final String CANCEL="org.xrgpu.benchmarks.CANCEL";
    static final int NOTIFICATION=1;
    static final long MAX_DURATION_MS=30*60*1000L;
    final Handler main=new Handler(Looper.getMainLooper());
    final AtomicBoolean cancelled=new AtomicBoolean();
    Thread worker;
    String runId;
    PowerManager.WakeLock wake;
    long lastNotification;
    boolean finished;
    final Runnable timeout=()->{cancel();if(worker!=null)worker.interrupt();};
    final Runnable environment=new Runnable(){public void run(){
        if(runId==null)return;
        try{PowerManager power=getSystemService(PowerManager.class);
            JSONObject state=new JSONObject().put("run_id",runId).put("thermal_status",power.getCurrentThermalStatus())
                .put("charging",getSystemService(BatteryManager.class).isCharging()).put("display_interactive",power.isInteractive());
            if(power.getCurrentThermalStatus()>=PowerManager.THERMAL_STATUS_SEVERE)state.put("stop_reason","Thermal state severe; measurements stopped");
            Fileset.write(new File(getFilesDir(),"runs/"+runId+"/environment.json"),state);
        }catch(Exception error){Log.e("xrBench","Cannot record benchmark environment",error);}
        main.postDelayed(this,1000);
    }};
    void cancel(){
        cancelled.set(true);
        if(runId!=null)try{File file=new File(getFilesDir(),"runs/"+runId+"/cancel");file.getParentFile().mkdirs();file.createNewFile();}catch(IOException error){Log.e("xrBench","Cannot preserve cancellation",error);}
    }

    @Override public void onCreate(){
        super.onCreate();
        NotificationManager notifications=getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel(CHANNEL,
            "xrBench benchmark",NotificationManager.IMPORTANCE_LOW));
    }
    Notification notification(String message){
        Notification.Builder builder=new Notification.Builder(this,CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("xrBench is benchmarking your device")
            .setContentText(message).setOngoing(true).setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE);
        if(runId!=null){
            PendingIntent stop=PendingIntent.getService(this,0,
                new Intent(this,BenchmarkService.class).setAction(CANCEL).putExtra("run_id",runId),
                PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
            builder.addAction(new Notification.Action.Builder(null,"Stop benchmark",stop).build());
        }
        return builder.build();
    }
    void progress(String message){
        // Keep notification work outside the measurement loop and rate limited.
        long now=SystemClock.elapsedRealtime();
        if(now-lastNotification<1000)return;
        lastNotification=now;
        getSystemService(NotificationManager.class).notify(NOTIFICATION,notification(message));
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent==null){stopSelf(startId);return START_NOT_STICKY;}
        if(CANCEL.equals(intent.getAction())){
            if(runId!=null&&runId.equals(intent.getStringExtra("run_id")))cancel();
            return START_NOT_STICKY;
        }
        // An uncertain browser launch is recovered, never replayed on Android restart.
        if(worker!=null||Runner.active.get())return START_NOT_STICKY;
        final String id=intent.getStringExtra("run_id"),probe=intent.getStringExtra("probe_id");
        try{
            if(probe!=null){
                if(!probe.matches("probe-[0-9a-f]{32}"))throw new IllegalArgumentException("Invalid probe identity");
            }else{
                Checks.identity(id);
                if(new File(getFilesDir(),"runs/"+id+"/report.json").exists()){
                    stopSelf(startId);return START_NOT_STICKY;
                }
                JSONObject command=Fileset.read(new File(getFilesDir(),"commands/"+id+".json"));
                if(!"isolated".equals(command.getString("mode")))throw new IllegalArgumentException("Foreground worker requires isolated compute");
                runId=id;
            }
            Notification notification=notification(probe!=null?"Checking device capabilities":"You can take off the headset. Keep USB connected.");
            if(Build.VERSION.SDK_INT>=34)startForeground(NOTIFICATION,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(NOTIFICATION,notification,ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            PowerManager power=getSystemService(PowerManager.class);
            wake=power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"xrBench:benchmark");
            wake.setReferenceCounted(false);
            wake.acquire(MAX_DURATION_MS+60000);
            main.postDelayed(timeout,MAX_DURATION_MS);
            if(runId!=null)environment.run();
            worker=new Thread(()->{
                try{
                    if(probe!=null)Fileset.write(new File(getFilesDir(),"profiles/"+probe+".json"),
                        (BuildConfig.PUBLIC_NATIVE?NativeOperatorRunner.profile(this):DeviceProfile.collect(this)).put("probe_id",probe));
                    else if(BuildConfig.PUBLIC_NATIVE)NativeOperatorRunner.run(this,id);
                    else new Runner(this,id,cancelled,this::progress).run();
                }catch(Exception|LinkageError error){
                    Log.e("xrBench","Native benchmark worker failed",error);
                    if(probe!=null)try{Fileset.write(new File(getFilesDir(),"profiles/"+probe+".json"),
                        new JSONObject().put("probe_id",probe).put("error","Device capability check failed: "+error));}catch(Exception ignored){}
                    else if(id!=null)try{Fileset.write(new File(getFilesDir(),"runs/"+id+"/startup-error.json"),
                        new JSONObject().put("run_id",id).put("error","Native benchmark could not start: "+error));}catch(Exception ignored){}
                }finally{main.post(()->finish(startId));}
            },"xrbench-isolated-benchmark");
            worker.start();
        }catch(Exception|LinkageError error){
            Log.e("xrBench","Cannot start native benchmark worker",error);
            if(id!=null&&id.matches("run-[0-9a-f]{32}"))try{Fileset.write(new File(getFilesDir(),"runs/"+id+"/startup-error.json"),new JSONObject().put("run_id",id).put("error","Native benchmark could not start: "+error));}catch(Exception ignored){}
            finish(startId);
        }
        return START_NOT_STICKY;
    }
    void finish(int startId){
        finished=true;
        main.removeCallbacks(timeout);
        main.removeCallbacks(environment);
        if(wake!=null&&wake.isHeld())wake.release();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }
    @Override public void onDestroy(){
        if(!finished)cancel();
        main.removeCallbacks(timeout);
        main.removeCallbacks(environment);
        if(worker!=null)worker.interrupt();
        if(wake!=null&&wake.isHeld())wake.release();
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
