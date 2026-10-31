"use strict";
// 只绘制独立装饰，不能访问请求、业务状态、主仪表或报告。
class CleanupEffect {
  constructor(bar, note) {this.bar=bar;this.note=note;this.phase=null;this.frame=null;}
  start(value) {
    this.stop();this.phase="emptying";this.origin=this.value=value||0;
    this.started=performance.now();this.ready=false;
    this.frame=requestAnimationFrame(now=>this.tick(now));
  }
  finish(value) {this.target=value||0;this.ready=true;}
  stop() {
    if(this.frame!=null)cancelAnimationFrame(this.frame);
    this.frame=null;this.phase=null;
    this.bar.style.width="0%";this.note.textContent="过程动效 · 不代表内存指标";
  }
  tick(now) {
    this.frame=null;if(!this.phase)return;
    if(this.phase==="emptying") {
      const p=Math.min(1,Math.max(0,(now-this.started)/3000));
      this.value=this.origin*(1-p*p*(3-2*p));
      if(p===1){this.phase="waiting";this.started=now;}
    }else if(this.phase==="waiting"&&this.ready&&now-this.started>=200) {
      this.phase="returning";this.started=now;
    }else if(this.phase==="returning") {
      const p=Math.min(1,Math.max(0,(now-this.started)/1200));
      this.value=this.target*(1-(1-p)**3);
      if(p===1){this.stop();return;}
    }
    this.bar.style.width=`${this.value}%`;
    this.note.textContent="过程动效 · "+{emptying:"缓慢归零",waiting:"等待通知",returning:"收束"}[this.phase];
    this.frame=requestAnimationFrame(now=>this.tick(now));
  }
}
