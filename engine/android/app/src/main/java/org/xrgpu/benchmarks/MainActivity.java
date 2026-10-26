package org.xrgpu.benchmarks;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.TextView;
import java.io.File;

public final class MainActivity extends Activity {
    TextView label;
    @Override public void onCreate(Bundle state){
        super.onCreate(state);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        label=new TextView(this);label.setTextSize(22);label.setPadding(32,32,32,32);setContentView(label);
        String probe=getIntent().getStringExtra("probe_id");
        if(probe!=null){
            if(!probe.matches("probe-[0-9a-f]{32}")||Runner.active.get()){label.setText("Capability check unavailable during a run");return;}
            try{startForegroundService(new Intent(this,BenchmarkService.class).putExtra("probe_id",probe));finish();}
            catch(Exception error){label.setText("Cannot check device capabilities: "+error);}
            return;
        }
        String id=getIntent().getStringExtra("run_id");
        if(id==null){label.setText("xrGPU native benchmarks\nConnect USB and press Run on the website.");return;}
        try{
            Checks.identity(id);
            if(Runner.active.get())throw new IllegalStateException("Another run is active");
            if(new File(getFilesDir(),"runs/"+id+"/report.json").exists())throw new IllegalStateException("This run already has evidence; reconnect to recover it");
            if(Fileset.read(new File(getFilesDir(),"commands/"+id+".json")).getString("mode").equals("xr")){
                if(BuildConfig.PUBLIC_NATIVE)throw new IllegalArgumentException("The native operator runner requires isolated compute");
                startActivity(new Intent(this,XrActivity.class).putExtra("run_id",id));finish();return;
            }
            startForegroundService(new Intent(this,BenchmarkService.class).putExtra("run_id",id));
            finish();
        }catch(Exception error){label.setText("Cannot start benchmark: "+error);}
    }
}
