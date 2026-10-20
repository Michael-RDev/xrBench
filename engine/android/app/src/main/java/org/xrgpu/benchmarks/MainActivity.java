package org.xrgpu.benchmarks;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.TextView;
import java.io.File;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity {
    final AtomicBoolean cancel=new AtomicBoolean();Thread worker;TextView label;
    @Override public void onCreate(Bundle state){
        super.onCreate(state);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        label=new TextView(this);label.setTextSize(22);label.setPadding(32,32,32,32);setContentView(label);
        String probe=getIntent().getStringExtra("probe_id");
        if(probe!=null){
            if(!probe.matches("probe-[0-9a-f]{32}")||Runner.active.get()){label.setText("Capability check unavailable during a run");return;}
            worker=new Thread(()->{try{
                Fileset.write(new File(getFilesDir(),"profiles/"+probe+".json"),DeviceProfile.collect(this).put("probe_id",probe));
                runOnUiThread(this::finish);
            }catch(Exception|LinkageError error){runOnUiThread(()->label.setText("Capability check failed: "+error));}},"capability-probe");
            worker.start();return;
        }
        String id=getIntent().getStringExtra("run_id");
        if(id==null){label.setText("xrGPU native benchmarks\nConnect USB and press Run on the local website.");return;}
        try{
            Checks.identity(id);
            if(Runner.active.get())throw new IllegalStateException("Another run is active");
            if(Fileset.read(new File(getFilesDir(),"commands/"+id+".json")).getString("mode").equals("xr")){
                startActivity(new Intent(this,XrActivity.class).putExtra("run_id",id));finish();return;
            }
            Runner runner=new Runner(this,id,cancel,s->runOnUiThread(()->label.setText(s)));
            worker=new Thread(()->{try{runner.run();}finally{runOnUiThread(this::finish);}},"native-benchmark");worker.start();
        }catch(Exception error){label.setText("Cannot start benchmark: "+error);}
    }
    @Override protected void onPause(){if(worker!=null)cancel.set(true);super.onPause();}
    @Override protected void onDestroy(){cancel.set(true);super.onDestroy();}
}
