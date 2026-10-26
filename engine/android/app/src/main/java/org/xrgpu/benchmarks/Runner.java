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
    String runPhase="preparation";
    long caseStarted=0;
    boolean cpu(){return config.optString("backend","executorch_vulkan").contains("cpu");}
    JSONObject backendSpec(JSONObject spec){JSONObject backends=spec.optJSONObject("backends");return backends==null?spec:backends.optJSONObject(config.optString("backend","executorch_vulkan"));}
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
        if(canceled.get()||new File(directory,"cancel").exists()||Thread.currentThread().isInterrupted())throw new InterruptedException("Canceled by user or benchmark worker shutdown");
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
            .put("current_samples",current==null?0:current.getJSONArray("samples").length())
            .put("processor",cpu()?"cpu":"gpu").put("phase",runPhase).put("updated_at",Instant.now().toString())
            .put("elapsed_ms",SystemClock.elapsedRealtime()-started).put("target_samples",plan==null?0:plan.samples());
        report.put("progress",state).put("diagnostics",ledger.snapshot());
        try{Fileset.write(new File(directory,"report.json"),report);Fileset.write(new File(directory,"progress.json"),state);}
        catch(IOException error){throw new SessionFailure("Could not preserve native evidence",error);}
        progress.accept(label);
        }
    }
    final class Model implements AutoCloseable {
        final ComputeBackend backend;final byte[][] inputs;final int[][] shapes;final String[] types;final float[] expected;final JSONObject spec;double lastInferenceMs;
        Model(JSONObject spec) throws Exception {this(spec,"stock");}
        Model(JSONObject spec,String variant) throws Exception {
            try(TimingLedger.Scope scope=ledger.enter("preparation")){
            this.spec=spec;JSONArray array=spec.getJSONArray("inputs");inputs=new byte[array.length()][];shapes=new int[array.length()][];types=new String[array.length()];
            for(int i=0;i<array.length();i++){JSONObject s=array.getJSONObject(i);inputs[i]=Files.readAllBytes(new File(assets.root,s.getString("file")).toPath());
                JSONArray dims=s.getJSONArray("shape");shapes[i]=new int[dims.length()];for(int j=0;j<dims.length();j++)shapes[i][j]=dims.getInt(j);types[i]=s.getString("dtype");}
            expected=assets.floats(spec.getString("expected"));backend=ComputeBackend.open(config,spec,new File(assets.root,backendSpec(spec)==null?"":backendSpec(spec).optString("program","")).getPath(),variant,inputs,shapes,types);
            }
        }
        float[] infer(){long begin=System.nanoTime();try{return backend.infer();}finally{lastInferenceMs=(System.nanoTime()-begin)/1e6;}}
        JSONObject validate(float[] actual) throws Exception {
            try(TimingLedger.Scope scope=ledger.enter("validation")){
            long wallBegin=System.nanoTime();
            actual=backend.output(actual);
            JSONObject executed=backend.stats();
            if(current!=null)current.put("validation_execution",executed);
            JSONArray dispatches=executed.optJSONArray("dispatches");
            if(dispatches!=null&&kernelManifest!=null)for(int i=0;i<dispatches.length();i++){
                String name=dispatches.getJSONObject(i).optString("kernel_name");
                if(kernelManifest.getJSONObject("shaders").has(name))report.getJSONObject("shaders").put(name,kernelManifest.getJSONObject("shaders").get(name));
            }
            JSONArray shape=executed.getJSONArray("output_shape");
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
        public void close(){backend.close();}
    }
    JSONObject row(JSONObject spec,String phase,int trial) throws Exception {
        JSONObject r=new JSONObject().put("case",spec.getString("id")).put("phase",phase).put("trial",trial)
            .put("operation",spec.getString("operation")).put("inputs",spec.get("inputs")).put("params",spec.get("params"))
            .put("precision","fp32").put("fixture_seed",spec.optLong("fixture_seed",assets.suite.getLong("seed"))).put("status","interrupted").put("samples",new JSONArray())
            .put("thermal_start",power.getCurrentThermalStatus()).put("charging_start",battery.isCharging());
        results.put(r);current=r;return r;
    }
    boolean begin(JSONObject spec,int index) throws Exception {
        caseIndex=index;current=null;caseStarted=SystemClock.elapsedRealtime();runPhase="preparation";check();persist("Preparing");
        String backend=config.optString("backend","executorch_vulkan");
        if(backend.endsWith("_resident"))return true;
        JSONObject exported=backendSpec(spec);
        if(exported==null||!exported.optString("export_status").equals("ready")){
            row(spec,xr?"baseline":"isolated",0).put("status","skipped").put("reason",exported==null?"Backend export unavailable":exported.optString("reason","Export unavailable"));return false;}
        if(!cpu()&&(!report.getJSONObject("device").optBoolean("vulkan")||!exported.getJSONObject("coverage").getBoolean("fully_delegated"))){
            row(spec,xr?"baseline":"isolated",0).put("status","skipped").put("reason","Incomplete GPU delegation");return false;}
        return true;
    }
    void failed(JSONObject spec,Exception error) throws Exception {
        if(current==null)row(spec,xr?"baseline":"isolated",0);
        current.put("status","failed").put("error",error.toString());
    }
    void finished(JSONObject spec) throws Exception {runPhase="between_cases";if(current!=null)current.put("case_wall_ms",SystemClock.elapsedRealtime()-caseStarted);completed.add(spec.getString("id"));persist("Finished");}
    void observe(JSONObject r) throws Exception {
        if(power.getCurrentThermalStatus()!=r.getInt("thermal_start")||battery.isCharging()!=r.getBoolean("charging_start"))r.put("thermal_discontinuity",true);
    }
    float[] sample(Model model,JSONObject r) throws Exception {
        try(TimingLedger.Scope scope=ledger.enter("measurement")){
        check();long start=SystemClock.elapsedRealtimeNanos();float[] actual=model.infer();double elapsed=(SystemClock.elapsedRealtimeNanos()-start)/1e6;
        JSONObject sample=model.backend.stats().put("execution_ms",elapsed)
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
        runPhase="validation";persist("Checking correctness");check();JSONObject evidence=model.validate(model.infer());if(current!=null)current.put("correctness_before",evidence);
        runPhase="warmup";persist("Warming kernels");
        for(int i=0;i<plan.warmups();i++){check();model.infer();}
        }
    }
    void isolated(JSONObject spec) throws Exception {
        JSONObject r=row(spec,"isolated",0);persist("Measuring");
        try(Model model=new Model(spec)) {
            warm(model);runPhase="measurement";persist("Measuring");float[] actual=null;
            for(int i=0;i<plan.samples();i++){actual=sample(model,r);if((i+1)%25==0)persist("Measuring");}
            runPhase="validation";persist("Checking correctness");
            r.put("correctness_after",model.validate(actual));r.put("status","passed");
        }
    }
    void experiment(JSONObject spec) throws Exception {
        int pairs=config.getInt("experiment_pairs");long seed=config.getLong("experiment_seed");
        String candidate=config.getString("experiment").equals("aa")?"stock":"candidate";
        // Keep both models loaded so samples don't include allocation, pipeline creation or warmup.
        try(Model stock=new Model(spec,"stock");Model alternative=new Model(spec,candidate)){
            Model[] models={stock,alternative};int[] order=KernelExperiment.order(pairs,seed);
            for(int pair=0;pair<pairs;pair++)for(int position=0;position<2;position++){
                int arm=position==0?order[pair]:1-order[pair];Model model=models[arm];
                JSONObject r=row(spec,"isolated",pair*2+arm).put("pair_index",pair).put("position",position)
                    .put("arm",arm==0?"baseline":"comparison").put("variant",arm==0?"stock":candidate);
                persist("Kernel experiment · pair "+(pair+1)+" / "+pairs+" · "+r.getString("arm"));
                warm(model);runPhase="measurement";persist("Measuring");float[] actual=null;
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
            String backendName=config.optString("backend","executorch_vulkan");
            JSONObject caps=new JSONObject().put("model",Build.MODEL).put("manufacturer",Build.MANUFACTURER).put("os",Build.FINGERPRINT);
            report=new JSONObject().put("schema_version",2).put("run_id",id).put("status","running").put("started_at",Instant.now().toString())
                .put("execution_location",config.optString("execution_target","unknown")).put("backend",config.optString("backend","executorch_vulkan")).put("configuration",config)
                .put("suite_revision",config.getString("suite_revision")).put("runtime_revision",config.getString("runtime_revision"))
                .put("device",caps).put("results",results).put("timing_scope","execution_ms: JNI staging + native forward + output copy; model_ms: native forward including delegate staging; gpu_ms: sum of operation timestamp intervals, not critical-path latency");
            report.put("processor",cpu()?"cpu":"gpu");
            report.put("execution_context",context instanceof BenchmarkService?"foreground_service":"xr_activity")
                .put("display_interactive_at_start",power.isInteractive());
            if(cpu())report.put("threading_policy","single calling thread")
                .put("timing_scope",backendName.equals("cpu_resident")?"execution_ms: JNI invocation + CPU matmul; model_ms: CPU matmul; persistent buffers; output copy outside samples":"execution_ms: JNI staging + portable CPU forward + output copy; model_ms: portable CPU forward");
            persist("Verifying native layer assets");
            JSONObject nativeCaps=DeviceProfile.collect(context,!cpu());
            String target=config.optString("execution_target","unknown");
            if(!Arrays.asList("unknown","headset","glasses","phone","compute_puck").contains(target))throw new SessionFailure("Invalid execution target");
            for(Iterator<String> keys=nativeCaps.keys();keys.hasNext();){String key=keys.next();caps.put(key,nativeCaps.get(key));}
            caps.put("execution_target",target).put("target_provenance",target.equals("unknown")?"Not supplied":"Controller supplied; measured processor is this Android device");
            if(!nativeCaps.getString("runtime_revision").equals(config.getString("runtime_revision"))||!nativeCaps.getString("runner_revision").equals(config.getString("runner_revision")))throw new SessionFailure("Native runtime identity mismatch; rebuild APK");
            JSONObject support=nativeCaps.optJSONObject("backends");
            if(support==null||support.optJSONObject(backendName)==null||!support.getJSONObject(backendName).optBoolean("compatible"))throw new SessionFailure("Selected backend requirements unavailable on this device");
            if(xr&&(!Build.MODEL.equals("Quest 3")||!backendName.equals("executorch_vulkan")||(!target.equals("unknown")&&!target.equals("headset"))))throw new SessionFailure("XR scene requires the Quest 3 profile");
            if(backendName.equals("vulkan_resident"))report.put("timing_scope","execution_ms: JNI dispatch + submission + completion wait; model_ms: native command encoding + submission + wait; persistent buffers, no per-sample host tensor upload or readback; gpu_span_ms: dispatch sequence");
            assets=new Fileset(context);
            try(InputStream in=context.getAssets().open("kernel-manifest.json")){
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] data=new byte[65536];int n;
                while((n=in.read(data))!=-1)bytes.write(data,0,n);
                kernelManifest=new JSONObject(bytes.toString("UTF-8"));
            }
            JSONObject expectedPackage=config.getJSONObject("kernel_package");
            JSONObject builtPackage=kernelManifest.getJSONObject("kernel_package");
            for(String key:Arrays.asList("repository","commit","package_id","package_path","content_sha256"))
                if(!builtPackage.getString(key).equals(expectedPackage.getString(key)))throw new SessionFailure("Kernel package identity mismatch; rebuild APK");
            JSONObject kernelBuild=new JSONObject(kernelManifest.toString());kernelBuild.remove("shaders");
            report.put("shaders",new JSONObject()).put("kernel_build",kernelBuild);
            String experiment=config.optString("experiment","none");
            if(!experiment.equals("none")){
                if(xr||config.getString("preset").equals("sustained")||(!experiment.equals("candidate")&&!experiment.equals("aa")))throw new SessionFailure("Invalid kernel experiment configuration");
                if(!kernelManifest.getString("candidate_revision").equals(config.getString("candidate_revision")))throw new SessionFailure("Kernel build identity mismatch");
                report.put("experiment",new JSONObject().put("version",1).put("kind",experiment).put("pairs",config.getInt("experiment_pairs"))
                    .put("seed",config.getLong("experiment_seed")).put("primary_metric","execution_ms").put("minimum_improvement_percent",5)
                    .put("validation_control",config.optString("validation_control","none")).put("scope",report.getString("timing_scope")));
            }
            if(!assets.suite.getString("suite_revision").equals(config.getString("suite_revision")))throw new SessionFailure("Suite revision mismatch; rebuild APK");
            plan=new SuitePlan(assets.suite.getJSONArray("cases"),config.getJSONArray("cases"),config.getString("preset"),xr);
            if(!experiment.equals("none"))for(int i=0;i<plan.cases.length();i++)if(!plan.cases.getJSONObject(i).getString("operation").equals("matmul"))throw new SessionFailure("First kernel experiment supports matmul only");
            if(backendName.endsWith("_resident")){
                if(xr||plan.preset.equals("sustained"))throw new SessionFailure("Resident backend requires isolated Smoke or Standard");
                for(int i=0;i<plan.cases.length();i++)if(!plan.cases.getJSONObject(i).getString("operation").equals("matmul"))throw new SessionFailure("Resident backend requires matmul");
            }
            String control=config.optString("validation_control","none");
            if(!Arrays.asList("none","incorrect","slower").contains(control)||(!control.equals("none")&&(!experiment.equals("candidate"))))throw new SessionFailure("Invalid hardware validation control");
            deadline=started+plan.deadlineMs();report.put("deadline_ms",plan.deadlineMs()).put("planned_window_ms",plan.plannedWindowMs());
            if(xr){long until=SystemClock.elapsedRealtime()+120000;while(true){if(canceled.get()||new File(directory,"cancel").exists())throw new InterruptedException("Canceled during XR startup");JSONObject scene=new JSONObject(Native.scene(false,false));
                if(!scene.optString("error").isEmpty())throw new SessionFailure(scene.getString("error"));
                if(scene.optBoolean("ready")&&scene.optBoolean("focused")&&Math.abs(scene.optDouble("refresh_hz",0)-72)<.1)break;
                if(SystemClock.elapsedRealtime()>until)throw new SessionFailure("Put on the headset: XR did not become focused at 72 Hz");Thread.sleep(100);}
                report.put("scene_revision","cube-grid-v1-1440x1440-gles3").put("scene_settings",new JSONObject().put("width_per_eye",1440).put("height_per_eye",1440).put("renderer","OpenGL ES 3").put("refresh_hz",new JSONObject(Native.scene(false,false)).get("refresh_hz")))
                    .put("refresh_hz",72).put("inference_hz",10);}
            if(!cpu()){runPhase="calibration";calibrate(caps);}

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
            try {if(report!=null){caseIndex=-1;runPhase="finished";report.put("finished_at",Instant.now().toString()).put("display_interactive_at_finish",power.isInteractive());persist("Run "+report.getString("status")+" · "+completed.size()+" / "+config.getJSONArray("cases").length()+" tests finished");}}catch(Exception error){progress.accept("Could not save report: "+error);}
            active.set(false);
        }
    }
}
