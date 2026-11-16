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
            if(!probe.matches("probe-[0-9a-f]{32}")){finish();return;}
            if(Runner.active.get()){
                StartupDiagnostic.write(getFilesDir(),probe,null,"native_busy",StartupDiagnostic.BUSY);finish();return;
            }
            try{startForegroundService(new Intent(this,BenchmarkService.class).putExtra("probe_id",probe));finish();}
            catch(Exception|LinkageError error){
                StartupDiagnostic.write(getFilesDir(),probe,null,"native_start_failed","Cannot check device capabilities: "+error);finish();
            }
            return;
        }
        String id=getIntent().getStringExtra("run_id");
        if(id==null){label.setText("xrGPU native benchmarks\nConnect USB and press Run on the website.");return;}
        try{
            Checks.identity(id);
            if(Runner.active.get()){
                StartupDiagnostic.write(getFilesDir(),null,id,"native_busy",StartupDiagnostic.BUSY);finish();return;
            }
            if(new File(getFilesDir(),"runs/"+id+"/report.json").exists())throw new IllegalStateException("This run already has evidence; reconnect to recover it");
            if(Fileset.read(new File(getFilesDir(),"commands/"+id+".json")).getString("mode").equals("xr")){
                if(BuildConfig.PUBLIC_NATIVE)throw new IllegalArgumentException("The native operator runner requires isolated compute");
                startActivity(new Intent(this,XrActivity.class).putExtra("run_id",id));finish();return;
            }
            startForegroundService(new Intent(this,BenchmarkService.class).putExtra("run_id",id));
            finish();
        }catch(Exception|LinkageError error){
            StartupDiagnostic.write(getFilesDir(),null,id,"native_start_failed","Cannot start benchmark: "+error);finish();
        }
    }
}
