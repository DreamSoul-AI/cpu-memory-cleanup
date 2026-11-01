"use strict";
// 业务完成后的显示流程，只通过回调取得一次新读数，不持有清理接口。
class CleanupEffect {
  constructor(render,refresh){this.render=render;this.refresh=refresh;this.phase=null;this.frame=null;this.cycle=0;}
  start(value){
    this.stop();
    if(value==null||!Number.isFinite(value))return;
    this.origin=value;this.value=value;this.phase="emptying";this.started=performance.now();
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(now=>this.tick(now));
  }
  stop(){
    this.cycle++;
    if(this.frame!=null)cancelAnimationFrame(this.frame);
    this.frame=null;this.phase=null;this.render(null,"idle");
  }
  tick(now){
    this.frame=null;if(!this.phase)return;
    if(this.phase==="emptying"){
      const p=Math.min(1,Math.max(0,(now-this.started)/750));this.value=this.origin*(1-p);
      if(p===1){
        this.phase="refreshing";this.render(0,this.phase);
        const cycle=this.cycle;
        Promise.resolve().then(()=>this.cycle===cycle?this.refresh():null).then(value=>{
          if(this.cycle!==cycle||this.phase!=="refreshing")return;
          if(value==null||!Number.isFinite(value)){this.stop();return;}
          this.target=value;this.phase="returning";this.started=performance.now();
          this.frame=requestAnimationFrame(time=>this.tick(time));
        }).catch(()=>{if(this.cycle===cycle)this.stop();});
        return;
      }
    }else if(this.phase==="returning"){
      const p=Math.min(1,Math.max(0,(now-this.started)/300));this.value=this.target*p;
      if(p===1){this.stop();return;}
    }
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(time=>this.tick(time));
  }
}
