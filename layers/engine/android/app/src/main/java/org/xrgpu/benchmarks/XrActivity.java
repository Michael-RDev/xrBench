package org.xrgpu.benchmarks;

import android.app.NativeActivity;
import android.os.Bundle;
import android.view.WindowManager;
import android.util.Log;
import java.util.concurrent.atomic.AtomicBoolean;

public final class XrActivity extends NativeActivity {
    final AtomicBoolean cancel=new AtomicBoolean();
    @Override public void onCreate(Bundle state){
        super.onCreate(state);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        new Thread(()->{try{new Runner(this,getIntent().getStringExtra("run_id"),cancel,s->Log.i("xrGPU",s)).run();}
            catch(Exception e){Log.e("xrGPU","Cannot start XR benchmark",e);}finally{runOnUiThread(this::finish);}},"native-xr-benchmark").start();
    }
    @Override protected void onPause(){cancel.set(true);super.onPause();}
    @Override protected void onDestroy(){cancel.set(true);super.onDestroy();}
}
