// One active request and one latest pending request. No automatic retries.
export type LaneResult<T>={key:string;status:"pending"|"ok"|"error";value?:T;error?:string};
type Job<Q>={key:string;request:Q;generation:number;due:number};
export class LatestLane<Q,T>{
  private generation=0;
  private wanted:Job<Q>|null=null;
  private active:AbortController|null=null;
  private timer:ReturnType<typeof setTimeout>|null=null;
  private closed=false;
  constructor(private run:(request:Q,signal:AbortSignal)=>Promise<T>,private emit:(result:LaneResult<T>|null)=>void,
    private debounceMs:number,private timeoutMs:number){}
  submit(key:string|null,request:Q|null){
    if(this.closed)return;
    this.generation++;
    this.wanted=key!==null&&request!==null?{key,request,generation:this.generation,due:Date.now()+this.debounceMs}:null;
    this.emit(this.wanted?{key:this.wanted.key,status:"pending"}:null);
    this.pump();
  }
  private pump(){
    if(this.timer!==null){clearTimeout(this.timer);this.timer=null;}
    if(this.closed||this.active||!this.wanted)return;
    const job=this.wanted,wait=job.due-Date.now();
    if(wait>0){this.timer=setTimeout(()=>{this.timer=null;this.pump();},wait);return;}
    this.wanted=null;const ctrl=new AbortController();this.active=ctrl;
    let timedOut=false;
    const valid=()=>!this.closed&&job.generation===this.generation;
    const timeout=setTimeout(()=>{timedOut=true;ctrl.abort();if(valid())this.emit({key:job.key,status:"error",error:"DEADLINE_EXCEEDED"});},this.timeoutMs);
    void Promise.resolve().then(()=>this.run(job.request,ctrl.signal)).then(value=>{
      if(valid()&&!timedOut)this.emit({key:job.key,status:"ok",value});
    }).catch(error=>{
      if(valid()&&!timedOut)this.emit({key:job.key,status:"error",error:error instanceof Error?error.message:"REQUEST_FAILED"});
    }).finally(()=>{
      clearTimeout(timeout);if(this.active===ctrl)this.active=null;this.pump();
    });
  }
  dispose(){this.closed=true;this.generation++;this.wanted=null;if(this.timer!==null)clearTimeout(this.timer);this.active?.abort();}
}
