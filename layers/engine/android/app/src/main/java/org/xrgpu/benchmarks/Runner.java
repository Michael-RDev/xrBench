package org.xrgpu.benchmarks;

import android.content.Context;
import android.os.*;
import org.json.*;
import java.io.*;
import java.nio.file.Files;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import org.xrgpu.benchmarks.SuitePlan.SessionFailure;

public final class Runner {
    public static final AtomicBoolean active=new AtomicBoolean();
    final Context context;final AtomicBoolean canceled;final Consumer<String> progress;
    final PowerManager power;final BatteryManager battery;final File directory;final JSONObject config;
    final String id;final boolean xr;final long started=SystemClock.elapsedRealtime();
    long deadline=started+1800000;int caseIndex=-1,sequence=0;
    Fileset assets;SuitePlan plan;JSONObject report;JSONArray results=new JSONArray();JSONObject current;
    final Set<String> completed=new HashSet<>();
    final TimingLedger ledger=new TimingLedger();
    JSONObject kernelManifest;
    Runner(Context context,String id,AtomicBoolean canceled,Consumer<String> progress) throws Exception {
        Checks.identity(id);this.context=context;this.id=id;this.canceled=canceled;this.progress=progress;
        directory=new File(context.getFilesDir(),"runs/"+id);
        config=Fileset.read(new File(context.getFilesDir(),"commands/"+id+".json"));
        if(!id.equals(config.getString("run_id")))throw new IllegalArgumentException("Command identity mismatch");
        xr=config.getString("mode").equals("xr");
        if(!xr&&!config.getString("mode").equals("isolated"))throw new IllegalArgumentException("Unknown mode");
        power=context.getSystemService(PowerManager.class);battery=context.getSystemService(BatteryManager.class);
    }
    void check() throws Exception {
        if(canceled.get()||new File(directory,"cancel").exists()||Thread.currentThread().isInterrupted())throw new InterruptedException("Canceled by user or activity lifecycle");
        if(SystemClock.elapsedRealtime()>deadline)throw new SessionFailure("Run exceeded its bounded deadline");
        if(power.getCurrentThermalStatus()>=PowerManager.THERMAL_STATUS_SEVERE)throw new SessionFailure("Thermal state severe; measurements stopped");
        if(xr){JSONObject scene=new JSONObject(Native.scene(false,false));
            if(!scene.optString("error").isEmpty())throw new SessionFailure(scene.getString("error"));
            if(!scene.optBoolean("focused")||Math.abs(scene.optDouble("refresh_hz",0)-72)>.1){
                if(current!=null)current.put("focus_lost",true);
                throw new SessionFailure("XR focus or 72 Hz refresh lost");}}
    }
    void persist(String message) throws Exception {
        try(TimingLedger.Scope scope=ledger.enter("reporting")){
        String label=caseIndex<0?message:"Test "+(caseIndex+1)+" of "+config.getJSONArray("cases").length()+" — "+config.getJSONArray("cases").getString(caseIndex)+" · "+message;
        JSONObject state=new JSONObject().put("run_id",id).put("status",report.get("status")).put("message",label)
            .put("sequence",++sequence).put("completed_cases",completed.size()).put("total_cases",config.getJSONArray("cases").length())
            .put("case_index",caseIndex+1).put("current_case",caseIndex<0?JSONObject.NULL:config.getJSONArray("cases").getString(caseIndex))
            .put("current_samples",current==null?0:current.getJSONArray("samples").length());
        report.put("progress",state).put("diagnostics",ledger.snapshot());
        try{Fileset.write(new File(directory,"report.json"),report);Fileset.write(new File(directory,"progress.json"),state);}
        catch(IOException error){throw new SessionFailure("Could not preserve native evidence",error);}
        progress.accept(label);
        }
    }
    final class Model implements AutoCloseable {
        final long handle;final byte[][] inputs;final int[][] shapes;final String[] types;final float[] expected;final JSONObject spec;double lastInferenceMs;
        Model(JSONObject spec) throws Exception {this(spec,"stock");}
        Model(JSONObject spec,String variant) throws Exception {
            try(TimingLedger.Scope scope=ledger.enter("preparation")){
            this.spec=spec;JSONArray array=spec.getJSONArray("inputs");inputs=new byte[array.length()][];shapes=new int[array.length()][];types=new String[array.length()];
            for(int i=0;i<array.length();i++){JSONObject s=array.getJSONObject(i);inputs[i]=Files.readAllBytes(new File(assets.root,s.getString("file")).toPath());
                JSONArray dims=s.getJSONArray("shape");shapes[i]=new int[dims.length()];for(int j=0;j<dims.length();j++)shapes[i][j]=dims.getInt(j);types[i]=s.getString("dtype");}
            expected=assets.floats(spec.getString("expected"));handle=Native.create(new File(assets.root,spec.getString("program")).getPath(),variant,!config.optString("profiling","trace").equals("latency"));
            }
        }
        float[] infer(){long begin=System.nanoTime();try{return Native.infer(handle,inputs,shapes,types);}finally{lastInferenceMs=(System.nanoTime()-begin)/1e6;}}
        JSONObject validate(float[] actual) throws Exception {
            try(TimingLedger.Scope scope=ledger.enter("validation")){
            long wallBegin=System.nanoTime();
            JSONArray shape=new JSONObject(Native.stats(handle)).getJSONArray("output_shape");
            long begin=System.nanoTime();
            JSONObject evidence=Checks.compare(actual,expected,shape,spec.getJSONArray("output_shape"),assets.suite.getDouble("atol"),assets.suite.getDouble("rtol"));
            double comparisonMs=(System.nanoTime()-begin)/1e6;
            if(current!=null){
                current.put("correctness",evidence);
                JSONArray checks=current.optJSONArray("validation_observations");if(checks==null){checks=new JSONArray();current.put("validation_observations",checks);}
                checks.put(new JSONObject().put("comparison_ms",comparisonMs).put("inference_ms",lastInferenceMs)
                    .put("total_ms",(System.nanoTime()-wallBegin)/1e6).put("passed",evidence.getBoolean("passed"))
                    .put("scope","Validation wall includes shape lookup and CPU comparison. Associated inference is separate and can reuse a measured sample; references were generated during export and loaded in preparation."));
            }
            if(!evidence.getBoolean("passed"))throw new IllegalStateException("Layer correctness failed: "+spec.getString("id"));
            return evidence;
            }
        }
        public void close(){Native.destroy(handle);}
    }
    JSONObject row(JSONObject spec,String phase,int trial) throws Exception {
        JSONObject r=new JSONObject().put("case",spec.getString("id")).put("phase",phase).put("trial",trial)
            .put("operation",spec.getString("operation")).put("inputs",spec.get("inputs")).put("params",spec.get("params"))
            .put("precision","fp32").put("status","interrupted").put("samples",new JSONArray())
            .put("thermal_start",power.getCurrentThermalStatus()).put("charging_start",battery.isCharging());
        results.put(r);current=r;return r;
    }
    boolean begin(JSONObject spec,int index) throws Exception {
        caseIndex=index;current=null;check();persist("Preparing");
        if(!report.getJSONObject("device").optBoolean("vulkan")||!spec.getString("export_status").equals("ready")){
            row(spec,xr?"baseline":"isolated",0).put("status","skipped").put("reason",spec.optString("reason","Vulkan unavailable"));return false;}
        if(!spec.getJSONObject("coverage").getBoolean("fully_delegated")){
            row(spec,xr?"baseline":"isolated",0).put("status","skipped").put("reason","Incomplete GPU delegation");return false;}
        return true;
    }
    void failed(JSONObject spec,Exception error) throws Exception {
        if(current==null)row(spec,xr?"baseline":"isolated",0);
        current.put("status","failed").put("error",error.toString());
    }
    void finished(JSONObject spec) throws Exception {completed.add(spec.getString("id"));persist("Finished");}
    void observe(JSONObject r) throws Exception {
        if(power.getCurrentThermalStatus()!=r.getInt("thermal_start")||battery.isCharging()!=r.getBoolean("charging_start"))r.put("thermal_discontinuity",true);
    }
    float[] sample(Model model,JSONObject r) throws Exception {
        try(TimingLedger.Scope scope=ledger.enter("measurement")){
        check();long start=SystemClock.elapsedRealtimeNanos();float[] actual=model.infer();double elapsed=(SystemClock.elapsedRealtimeNanos()-start)/1e6;
        JSONObject sample=new JSONObject(Native.stats(model.handle)).put("execution_ms",elapsed)
            .put("elapsed_seconds",SystemClock.elapsedRealtime()/1000.0).put("thermal_status",power.getCurrentThermalStatus()).put("charging",battery.isCharging());
        JSONArray dispatches=sample.optJSONArray("dispatches");
        if(dispatches!=null&&kernelManifest!=null)for(int i=0;i<dispatches.length();i++){
            String name=dispatches.getJSONObject(i).optString("kernel_name");
            if(kernelManifest.getJSONObject("shaders").has(name))report.getJSONObject("shaders").put(name,kernelManifest.getJSONObject("shaders").get(name));
        }
        r.getJSONArray("samples").put(sample);observe(r);return actual;
        }
    }
    void warm(Model model) throws Exception {
        try(TimingLedger.Scope scope=ledger.enter("warmup")){
        check();JSONObject evidence=model.validate(model.infer());if(current!=null)current.put("correctness_before",evidence);
        for(int i=0;i<plan.warmups();i++){check();model.infer();}
        }
    }
    void isolated(JSONObject spec) throws Exception {
        JSONObject r=row(spec,"isolated",0);persist("Measuring");
        try(Model model=new Model(spec)) {
            warm(model);float[] actual=null;
            for(int i=0;i<plan.samples();i++){actual=sample(model,r);if((i+1)%25==0)persist("Measuring");}
            r.put("correctness_after",model.validate(actual));r.put("status","passed");
        }
    }
    void experiment(JSONObject spec) throws Exception {
        int pairs=config.getInt("experiment_pairs");long seed=config.getLong("experiment_seed");
        String candidate=config.getString("experiment").equals("aa")?"stock":"candidate";
        // Both models stay loaded; allocation, pipeline creation and warmup are outside samples.
        try(Model stock=new Model(spec,"stock");Model alternative=new Model(spec,candidate)){
            Model[] models={stock,alternative};int[] order=KernelExperiment.order(pairs,seed);
            for(int pair=0;pair<pairs;pair++)for(int position=0;position<2;position++){
                int arm=position==0?order[pair]:1-order[pair];Model model=models[arm];
                JSONObject r=row(spec,"isolated",pair*2+arm).put("pair_index",pair).put("position",position)
                    .put("arm",arm==0?"baseline":"comparison").put("variant",arm==0?"stock":candidate);
                persist("Kernel experiment · pair "+(pair+1)+" / "+pairs+" · "+r.getString("arm"));
                warm(model);float[] actual=null;
                for(int i=0;i<plan.samples();i++)actual=sample(model,r);
                r.put("correctness_after",model.validate(actual)).put("status","passed");
                persist("Kernel experiment block finished");
            }
        }
    }
    void xrCase(JSONObject spec) throws Exception {
        // Reuse the first baseline row for setup errors so every attempted case has evidence.
        JSONObject first=row(spec,"baseline",0);persist("Warming layer");
        try(Model model=new Model(spec)) {
            warm(model);JSONObject correctness=model.validate(model.infer());
            for(int pair=0;pair<plan.pairs();pair++)for(int arm=0;arm<2;arm++) {
                boolean loaded=(pair%2==0?arm==1:arm==0);
                JSONObject r=pair==0&&arm==0?first:row(spec,loaded?"loaded":"baseline",pair);current=r;
                r.put("correctness",correctness).put("correctness_before",correctness);
                String label=(loaded?"Scene + layer":"Scene baseline")+" · pair "+(pair+1)+" / "+plan.pairs();
                persist(label);Native.scene(true,true);
                long begin=SystemClock.elapsedRealtimeNanos(),end=begin+plan.windowSeconds()*1000000000L,nextProgress=begin+1000000000L;float[] actual=null;
                while(SystemClock.elapsedRealtimeNanos()<end){check();long start=SystemClock.elapsedRealtimeNanos();if(loaded)actual=sample(model,r);observe(r);
                    if(SystemClock.elapsedRealtimeNanos()>=nextProgress){persist(label);nextProgress=SystemClock.elapsedRealtimeNanos()+1000000000L;}
                    pace(start);}
                check();JSONObject scene=new JSONObject(Native.scene(false,true));r.put("xr",scene).put("measurement_seconds",(SystemClock.elapsedRealtimeNanos()-begin)/1e9)
                    .put("xr_metrics_available",scene.getJSONArray("frame_ms").length()>0&&scene.getJSONArray("frame_ms").length()==scene.getInt("rendered_frames"));
                if(loaded){correctness=model.validate(actual);r.put("correctness_after",correctness);}r.put("status","passed");persist("Finished XR window");
            }
        }
    }
    void pace(long start) throws InterruptedException {
        long remaining=100000000L-(SystemClock.elapsedRealtimeNanos()-start);
        if(remaining>0)try(TimingLedger.Scope scope=ledger.enter("pacing")){Thread.sleep(remaining/1000000,(int)(remaining%1000000));}
    }
    final class Resident implements AutoCloseable {
        final Model model;final JSONObject result,spec;final int index;
        Resident(Model model,JSONObject result,JSONObject spec,int index){this.model=model;this.result=result;this.spec=spec;this.index=index;}
        void activate(){caseIndex=index;current=result;}
        public void close(){model.close();}
    }
    void sustained() throws Exception {
        List<Resident> resident=new ArrayList<>();
        try {
            plan.runCases(new SuitePlan.Worker(){
                public void execute(JSONObject spec,int index) throws Exception {
                    if(!begin(spec,index)){Runner.this.finished(spec);return;}
                    JSONObject r=row(spec,"isolated",0);Model model=new Model(spec);
                    try{warm(model);resident.add(new Resident(model,r,spec,index));}catch(Exception|Error error){model.close();throw error;}
                    persist("Ready for sustained measurement");
                }
                public void failed(JSONObject spec,Exception error) throws Exception {Runner.this.failed(spec,error);Runner.this.finished(spec);}
            });
            long end=SystemClock.elapsedRealtimeNanos()+600000000000L;int n=0,cursor=0;Set<String> measured=new HashSet<>();
            while(!resident.isEmpty()){
                boolean unmeasured=false;for(Resident item:resident)if(!measured.contains(item.spec.getString("id")))unmeasured=true;
                if(SystemClock.elapsedRealtimeNanos()>=end&&!unmeasured)break;
                int i=cursor%resident.size();Resident item=resident.get(i);item.activate();long start=SystemClock.elapsedRealtimeNanos();
                try{sample(item.model,item.result);measured.add(item.spec.getString("id"));n++;cursor=i+1;}
                catch(InterruptedException|SessionFailure error){throw error;}
                catch(Exception error){failed(item.spec,error);finished(item.spec);resident.remove(i).close();cursor=i;}
                if(n%25==0)persist("Sustained measurement");pace(start);
            }
            for(Resident item:resident){item.activate();check();
                try{current.put("correctness_after",item.model.validate(item.model.infer()));current.put("status","passed");}
                catch(Exception error){failed(item.spec,error);}finished(item.spec);}
        } finally {for(Resident item:resident)item.close();}
    }
    void calibrate(JSONObject caps) throws Exception {
        check();
        try(TimingLedger.Scope scope=ledger.enter("calibration")){
            int thermal=power.getCurrentThermalStatus();boolean charging=battery.isCharging();
            // The cache lives in this headset's app-private storage: physical devices never share it.
            JSONObject identity=CalibrationPolicy.identity(caps,Build.FINGERPRINT,config.getString("mode"),thermal,charging);
            File cache=new File(context.getFilesDir(),"calibration-v1.json");JSONObject profile=null;
            if(!config.optString("calibration","auto").equals("refresh"))try{
                JSONObject saved=Fileset.read(cache);
                if(CalibrationPolicy.compatible(saved,identity))profile=saved;
            }catch(Exception ignored){}
            if(profile==null){
                persist("Calibrating device · up to 15 seconds");
                profile=new JSONObject(Native.calibrate(()->canceled.get()||new File(directory,"cancel").exists()
                    ||power.getCurrentThermalStatus()!=thermal||battery.isCharging()!=charging
                    ||power.getCurrentThermalStatus()>=PowerManager.THERMAL_STATUS_SEVERE));
                profile.put("identity",identity.toString()).put("thermal_start",thermal).put("charging_start",charging)
                    .put("created_at",Instant.now().toString()).put("runner_revision",caps.get("runner_revision")).put("mode",config.getString("mode"));
                if(power.getCurrentThermalStatus()!=thermal||battery.isCharging()!=charging)
                    profile.put("available",false).put("reason","Calibration conditions changed");
                if(profile.optBoolean("available"))try{Fileset.write(cache,profile);}catch(IOException ignored){}
            }
            report.put("calibration",profile);check();
        }
    }
    public void run() {
        if(!active.compareAndSet(false,true)){progress.accept("Another native run is active");return;}
        try {
            JSONObject caps=new JSONObject().put("model",Build.MODEL).put("manufacturer",Build.MANUFACTURER).put("os",Build.FINGERPRINT);
            report=new JSONObject().put("schema_version",2).put("run_id",id).put("status","running").put("started_at",Instant.now().toString())
                .put("execution_location","headset").put("backend","executorch_vulkan").put("configuration",config)
                .put("suite_revision",config.getString("suite_revision")).put("runtime_revision",config.getString("runtime_revision"))
                .put("device",caps).put("results",results).put("timing_scope","execution_ms: JNI staging + native forward + output copy; model_ms: native forward including delegate staging; gpu_ms: sum of operation timestamp intervals, not critical-path latency");
            persist("Verifying native layer assets");
            JSONObject nativeCaps=new JSONObject(Native.capabilities());
            for(Iterator<String> keys=nativeCaps.keys();keys.hasNext();){String key=keys.next();caps.put(key,nativeCaps.get(key));}
            if(!nativeCaps.getString("runtime_revision").equals(config.getString("runtime_revision"))||!nativeCaps.getString("runner_revision").equals(config.getString("runner_revision")))throw new SessionFailure("Native runtime identity mismatch; rebuild APK");
            assets=new Fileset(context);
            try(InputStream in=context.getAssets().open("kernel-manifest.json")){
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] data=new byte[65536];int n;
                while((n=in.read(data))!=-1)bytes.write(data,0,n);
                kernelManifest=new JSONObject(bytes.toString("UTF-8"));
            }
            JSONObject kernelBuild=new JSONObject(kernelManifest.toString());kernelBuild.remove("shaders");
            report.put("shaders",new JSONObject()).put("kernel_build",kernelBuild);
            // App installation identity correlates reports without collecting a USB serial.
            android.content.SharedPreferences prefs=context.getSharedPreferences("device-profile",Context.MODE_PRIVATE);
            String installation=prefs.getString("id",null);
            if(installation==null){installation=UUID.randomUUID().toString();prefs.edit().putString("id",installation).commit();}
            caps.put("installation_id",installation);
            String experiment=config.optString("experiment","none");
            if(!experiment.equals("none")){
                if(xr||config.getString("preset").equals("sustained")||(!experiment.equals("candidate")&&!experiment.equals("aa")))throw new SessionFailure("Invalid kernel experiment configuration");
                if(!kernelManifest.getString("candidate_revision").equals(config.getString("candidate_revision")))throw new SessionFailure("Kernel build identity mismatch");
                report.put("experiment",new JSONObject().put("version",1).put("kind",experiment).put("pairs",config.getInt("experiment_pairs"))
                    .put("seed",config.getLong("experiment_seed")).put("primary_metric","execution_ms").put("minimum_improvement_percent",5)
                    .put("scope","Same-session invocation, including staging; exploratory paired-block comparison"));
            }
            if(!assets.suite.getString("suite_revision").equals(config.getString("suite_revision")))throw new SessionFailure("Suite revision mismatch; rebuild APK");
            plan=new SuitePlan(assets.suite.getJSONArray("cases"),config.getJSONArray("cases"),config.getString("preset"),xr);
            if(!experiment.equals("none"))for(int i=0;i<plan.cases.length();i++)if(!plan.cases.getJSONObject(i).getString("operation").equals("matmul"))throw new SessionFailure("First kernel experiment supports matmul only");
            deadline=started+plan.deadlineMs();report.put("deadline_ms",plan.deadlineMs()).put("planned_window_ms",plan.plannedWindowMs());
            if(xr){long until=SystemClock.elapsedRealtime()+120000;while(true){if(canceled.get()||new File(directory,"cancel").exists())throw new InterruptedException("Canceled during XR startup");JSONObject scene=new JSONObject(Native.scene(false,false));
                if(!scene.optString("error").isEmpty())throw new SessionFailure(scene.getString("error"));
                if(scene.optBoolean("ready")&&scene.optBoolean("focused")&&Math.abs(scene.optDouble("refresh_hz",0)-72)<.1)break;
                if(SystemClock.elapsedRealtime()>until)throw new SessionFailure("Put on the headset: XR did not become focused at 72 Hz");Thread.sleep(100);}
                report.put("scene_revision","cube-grid-v1-1440x1440-gles3").put("scene_settings",new JSONObject().put("width_per_eye",1440).put("height_per_eye",1440).put("renderer","OpenGL ES 3").put("refresh_hz",new JSONObject(Native.scene(false,false)).get("refresh_hz")))
                    .put("refresh_hz",72).put("inference_hz",10);}
            calibrate(caps);
            try(TimingLedger.Scope scope=ledger.enter("other")){
            if(!xr&&plan.preset.equals("sustained"))sustained();
            else plan.runCases(new SuitePlan.Worker(){
                public void execute(JSONObject spec,int index) throws Exception {if(begin(spec,index)){if(!experiment.equals("none"))experiment(spec);else if(xr)xrCase(spec);else isolated(spec);}}
                public void failed(JSONObject spec,Exception error) throws Exception {Runner.this.failed(spec,error);}
                public void finished(JSONObject spec) throws Exception {Runner.this.finished(spec);}
            });
            }
            report.put("status","complete");
        } catch(Exception|LinkageError error) {
            try {if(current!=null&&current.optString("status").equals("interrupted"))current.put("error",error.toString());
                if(report!=null)report.put("status",error instanceof InterruptedException?"canceled":"failed").put("error",error.toString());progress.accept(error.toString());}catch(Exception ignored){}
        } finally {
            try {if(report!=null){caseIndex=-1;report.put("finished_at",Instant.now().toString());persist("Run "+report.getString("status")+" · "+completed.size()+" / "+config.getJSONArray("cases").length()+" tests finished");}}catch(Exception error){progress.accept("Could not save report: "+error);}
            active.set(false);
        }
    }
}
