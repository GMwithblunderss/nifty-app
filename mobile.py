"""
MOBILE APP LAYOUT - turns the dashboard into a phone-app style screen (like broker apps) on small screens.

  * Top bar: Nifty price, live/offline status, today's P&L and the sound button.
  * Bottom tab bar: Chart, Position, Signals, Auto Lab, More (Chart + panel, Scorecard, Trades, Settings, account).
  * Trade button: opens a slide-up panel with all the trade buttons. "Square off ALL" sits apart at the bottom
    (it still needs its usual second tap to confirm).
  * Chart fills the screen between the bars; drawing tools behind a Draw button; full-screen mode.
Only switches on for phones (narrower than 820 px, or a phone held sideways). Desktop is untouched.
Hook in nify_1.py:  HTML = mobile.inject(autolab.inject(HTML))   (if an anchor is missing the page is left as it was)
"""

VIEWPORT = ('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="theme-color" content="#0b0f17"><meta name="mobile-web-app-capable" content="yes">'
            '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">')

CSS = r"""
#mBar,#mTabs,#mFab,#mBack,.msheet,#mFullX{display:none}
body.m{--tabH:calc(60px + env(safe-area-inset-bottom));--barH:calc(58px + env(safe-area-inset-top));--m-bg:#0b0f17;--m-card:#121926;--m-line:#1f2a3a}
body.m{display:block!important;height:auto!important;min-height:100vh;overflow-x:hidden!important;overflow-y:auto!important;background:var(--m-bg);font-size:14px;
 padding-top:var(--barH);padding-bottom:calc(var(--tabH) + 8px);-webkit-tap-highlight-color:transparent;-webkit-text-size-adjust:100%}
body.m input,body.m select,body.m textarea{font-size:16px!important}
body.m button,body.m select{min-height:40px}
body.m .top,body.m .ctrl{display:none!important}
body.m .msheet .ctrl{display:grid!important}

/* ---- top bar ---- */
body.m #mBar{display:flex;position:fixed;left:0;right:0;top:0;z-index:60;height:var(--barH);padding:env(safe-area-inset-top) 14px 0;align-items:center;gap:10px;
 background:rgba(11,15,23,.92);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-bottom:1px solid var(--m-line)}
#mBar .mb-l{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.15}
#mBar .mb-t{font-size:11px;letter-spacing:.08em;color:var(--mut);font-weight:600}
#mBar #spotbig{font-size:19px!important;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#mBar #spotbig small{font-size:11.5px;margin-left:6px}
#mBar .mb-m{display:flex;flex-direction:column;align-items:flex-end;line-height:1.15;min-width:0}
#mBar #pnlbig{font-size:15px!important;font-weight:700;white-space:nowrap}
#mBar #pnlbig small{display:block;font-size:10.5px;margin:0;text-align:right}
#mBar #pfeed,#mBar #pst{font-size:10.5px;padding:2px 8px;border-radius:20px;white-space:nowrap}
#mBar #pst:empty,#mBar #pfeed:empty{display:none}
#mBar .mb-pills{display:flex;gap:4px;margin-top:3px}
#mBar #bsnd{min-height:36px;width:36px;padding:0;border-radius:10px;background:transparent;border-color:var(--m-line)}

/* ---- bottom tabs ---- */
body.m #mTabs{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:60;height:var(--tabH);padding-bottom:env(safe-area-inset-bottom);
 background:rgba(11,15,23,.96);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-top:1px solid var(--m-line)}
#mTabs button{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;background:none!important;border:0!important;border-radius:0;
 color:var(--mut);font-size:10.5px;font-weight:500;padding:6px 0 4px;min-height:0;transform:none!important}
#mTabs button svg{width:23px;height:23px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
#mTabs button.act{color:#4f8ff7}
#mTabs button.act svg{stroke-width:2.2}

/* ---- trade button + sheets ---- */
body.m #mFab{display:flex;position:fixed;right:14px;bottom:calc(var(--tabH) + 14px);z-index:61;align-items:center;gap:7px;padding:0 18px;height:48px;border-radius:24px;
 background:linear-gradient(135deg,#3b82f6,#2563eb);border:0;color:#fff;font-weight:700;font-size:15px;box-shadow:0 8px 24px #2563eb66,0 2px 6px #0008}
#mFab svg{width:18px;height:18px;stroke:#fff;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
body.m[data-view=settings] #mFab,body.m[data-view=bullset] #mFab,body.m[data-view=bearset] #mFab,body.m[data-view=genset] #mFab,body.m[data-view=directset] #mFab{display:none}
body.m[data-view=chart] #mFab,body.m[data-view=split] #mFab{display:none}
#mTradeBtn{display:none}
body.m .chartbar #mTradeBtn{display:inline-flex;align-items:center;gap:5px;order:-7;background:linear-gradient(135deg,#3b82f6,#2563eb);border:0;color:#fff;font-weight:700;padding:6px 14px!important;min-height:36px;border-radius:10px}
#mTradeBtn svg{width:15px;height:15px;stroke:#fff;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
body.m #mBack{display:block;position:fixed;inset:0;z-index:70;background:#000a;opacity:0;pointer-events:none;transition:opacity .2s}
body.m.sheet-open #mBack{opacity:1;pointer-events:auto}
body.m .msheet{display:block;position:fixed;left:0;right:0;bottom:0;z-index:71;max-height:86vh;overflow-y:auto;background:#121926;border-radius:20px 20px 0 0;
 border-top:1px solid #2a3850;padding:8px 16px calc(18px + env(safe-area-inset-bottom));transform:translateY(105%);transition:transform .25s cubic-bezier(.2,.8,.2,1);box-shadow:0 -10px 40px #000a}
body.m .msheet.open{transform:none}
.msheet .grab{width:40px;height:5px;border-radius:3px;background:#34445c;margin:4px auto 12px}
.msheet h3{margin:0 0 4px;font-size:17px;border:0}
.msheet .msub{color:var(--mut);font-size:12.5px;margin-bottom:14px}
.msheet .ctrl{grid-template-columns:1fr 1fr;gap:10px;padding:0!important;border:0!important;background:none!important}
.msheet .ctrl>*{width:100%;margin:0!important}
.msheet .ctrl button{min-height:50px;font-size:15px;font-weight:700;border-radius:12px}
.msheet #bexp{grid-column:1/-1;order:-2;min-height:46px;border-radius:12px}
.msheet #bauto,.msheet #bstop{font-weight:600!important;font-size:13.5px!important}
.msheet #sline{grid-column:1/-1;order:50;margin:2px 0 0!important;font-size:12.5px;color:var(--mut);line-height:1.4}
.msheet #sline:empty{display:none}
.msheet #bsq{grid-column:1/-1;order:99;margin-top:14px!important;min-height:52px}
.msheet .sqnote{grid-column:1/-1;order:100;text-align:center;font-size:11.5px;color:var(--mut)}
.mlist{display:flex;flex-direction:column;gap:2px;margin:0 -6px 14px}
.mlist button{display:flex;align-items:center;gap:14px;width:100%;text-align:left;background:none;border:0;border-radius:12px;padding:12px 10px;font-size:15.5px;min-height:52px}
.mlist button:active,.mlist button.act{background:#1a2436}
.mlist button .ic{width:34px;height:34px;border-radius:10px;background:#1b2638;display:flex;align-items:center;justify-content:center;font-size:17px}
.mlist button .ar{margin-left:auto;color:var(--mut)}
.macct{background:#0e1520;border:1px solid var(--m-line);border-radius:14px;padding:12px 14px;font-size:13px;line-height:1.6}
.macct>*{display:block}

/* ---- pages ---- */
body.m .main{display:block!important;height:auto}
body.m .page{padding:14px 14px 20px!important;overflow:visible!important}
body.m .right{border:0!important;padding:14px!important;overflow:visible!important;background:transparent}
body.m .cards,body.m #cards,body.m #nets,body.m #acctc,body.m #tstats,body.m #sigTop{grid-template-columns:1fr 1fr!important;gap:8px!important}
body.m .card{border-radius:14px;padding:11px 12px!important;background:var(--m-card)}
body.m .card .v{font-size:17px!important}
body.m .card .l{font-size:10.5px!important}
body.m #sigCards{grid-template-columns:1fr!important}
body.m h4{margin:18px 0 8px!important}
body.m table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:nowrap;max-width:100%;border:1px solid var(--m-line);border-radius:12px}
body.m td,body.m th{padding:9px 10px!important;font-size:12.5px!important}
body.m td:first-child,body.m th:first-child{position:sticky;left:0;background:#0f1622;z-index:1}
body.m .set,body.m #vSettings .set,body.m .srow{grid-template-columns:1fr!important}
body.m #settings{max-width:100%}
body.m #subnav{display:flex;overflow-x:auto;gap:6px}body.m #subnav>*{flex:none}
body.m #indDlg{width:100vw!important;max-height:88vh;top:auto!important;bottom:0;transform:none!important;left:0!important;border-radius:20px 20px 0 0}
body.m #indDlg .ibody{grid-template-columns:1fr!important;overflow-y:auto}
body.m #indlist{border-right:0!important;max-height:34vh}body.m #indq{width:100%!important}
body.m #help{display:none!important}
body.m #trigform{left:3vw!important;width:94vw!important}
body.m #toasts{left:12px;right:12px;bottom:calc(var(--tabH) + 74px)}
body.m .toast{max-width:none;border-radius:12px}
body.m a.btn{margin:4px 6px 4px 0;padding:9px 12px;border-radius:10px}
body.m #labBox [style*="max-width"]{max-width:100%!important}
body.m #labBox .cards{grid-template-columns:1fr 1fr!important}
body.m #vSwing{padding:10px 12px}body.m #vSwing .swbox{padding:10px}body.m #vSwing .swchart text{font-size:13px}

/* ---- chart ---- */
body.m .left{display:flex;flex-direction:column}
body.m .chartbar{flex-wrap:nowrap!important;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;gap:6px!important;padding:8px 10px!important;background:var(--m-bg);border-bottom:1px solid var(--m-line)}
body.m .chartbar::-webkit-scrollbar{display:none}
body.m .chartbar>*{flex:none}
body.m .chartbar #tfSeg{order:-6}body.m .chartbar #serSeg{order:-5}body.m .chartbar #mDraw{order:-4}body.m .chartbar #bInd{order:-3}body.m .chartbar #bLive{order:-2}body.m .chartbar #bFull{order:-1}
body.m .chartbar .seg button{padding:6px 11px!important;min-height:34px}
body.m .chartbar button.q,body.m #mDraw{padding:6px 11px!important;min-height:36px}
body.m #bHelp,body.m #hint{display:none!important}
body.m .cwrap{flex-direction:column}
body.m .tools{flex-direction:row!important;width:auto!important;overflow-x:auto;padding:5px 8px!important;border-right:0!important;border-bottom:1px solid var(--m-line);gap:6px}
body.m .tools>*{flex:none}
body.m:not(.mdraw) .tools,body.m:not(.mdraw) .chartbar label{display:none!important}
body.m.mdraw #mDraw{background:var(--acc);color:#fff;border-color:var(--acc)}
body.m #legend{font-size:11px!important;left:8px!important;top:6px!important;max-width:calc(100% - 70px);white-space:normal;line-height:1.35}
body.m .sub{height:100px!important}
body.m .phd{font-size:10.5px!important}
body.m #ovbar{flex-wrap:nowrap!important;overflow-x:auto}body.m #ovbar>*{flex:none}
body.m[data-view=chart]{padding-bottom:var(--tabH)}
body.m[data-view=chart] .right{display:none!important}
body.m.full{padding:0!important}
body.m.full #mBar,body.m.full #mTabs,body.m.full #mFab{display:none!important}
body.m.full #mFullX{display:block;position:fixed;left:12px;bottom:calc(46px + env(safe-area-inset-bottom));z-index:90;padding:9px 14px;border-radius:12px;background:#141d2bee;font-weight:600}

/* ---- phone held sideways: slimmer bars so the chart gets the room ---- */
@media (orientation:landscape){
 body.m{--tabH:calc(46px + env(safe-area-inset-bottom));--barH:calc(44px + env(safe-area-inset-top))}
 body.m #mBar .mb-t,body.m #mBar .mb-pills,body.m #mBar #pnlbig small{display:none}
 body.m #mBar #spotbig{font-size:17px!important}
 body.m #mTabs button{flex-direction:row;gap:7px;font-size:12px}
 body.m #mTabs button svg{width:19px;height:19px}
 body.m[data-view=chart] #ovbar{display:none!important}
 body.m .chartbar{padding:4px 10px!important}
 body.m .msheet{max-height:94vh}
}
"""

JS = r"""
/* ---------- phone app layout ---------- */
(function(){
 const MQ='(max-width:820px),(max-height:520px) and (pointer:coarse)';
 const M=()=>window.matchMedia(MQ).matches;
 const svg=p=>'<svg viewBox="0 0 24 24">'+p+'</svg>';
 const IC={chart:svg('<path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 15l3-4 3 2 5-6"/>'),
  position:svg('<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 12h18"/>'),
  signals:svg('<path d="M2 12h3l3-7 4 14 3-7h7"/>'),
  lab:svg('<path d="M9 3h6"/><path d="M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7 15h10"/>'),
  swing:svg('<path d="M2 15c2.5 0 2.5-3 5-3s2.5 3 5 3 2.5-3 5-3 2.5 3 5 3"/><path d="M2 20c2.5 0 2.5-3 5-3s2.5 3 5 3 2.5-3 5-3 2.5 3 5 3"/><path d="M12 3v6"/><path d="M9 6l3-3 3 3"/>'),
  more:svg('<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>'),
  bolt:svg('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>')};
 const TABS=[['chart','Chart'],['position','Position'],['signals','Signals'],['lab','Auto Lab'],['swing','Swing'],['more','More']];
 const MORE=[['split','◫','Chart + panel'],['score','🏆','Signal scorecard'],['trades','📒','Trades & history'],['settings','⚙️','Settings']];
 const MAIN=TABS.map(t=>t[0]);
 let built=false, moved=[], made=[];
 const $$=id=>document.getElementById(id);
 function mk(tag,props,parent){const e=document.createElement(tag);Object.assign(e,props||{});(parent||document.body).appendChild(e);made.push(e);return e;}
 function move(el,to){if(!el||!to)return;moved.push([el,el.parentNode,el.nextSibling]);to.appendChild(el);}
 function closeSheets(){document.body.classList.remove('sheet-open');document.querySelectorAll('.msheet.open').forEach(s=>s.classList.remove('open'));}
 function openSheet(id){closeSheets();const s=$$(id);if(!s)return;document.body.classList.add('sheet-open');requestAnimationFrame(()=>s.classList.add('open'));}
 function go(v){closeSheets();try{setView(v);}catch(e){}window.scrollTo(0,0);}
 function syncTabs(){const v=document.body.dataset.view;if(!built)return;
  $$('mTabs').querySelectorAll('button').forEach(b=>b.classList.toggle('act',b.dataset.v===v||(b.dataset.v==='more'&&!MAIN.includes(v))));
  document.querySelectorAll('#mSheetMore .mlist button').forEach(b=>b.classList.toggle('act',b.dataset.v===v));}
 function build(){if(built)return;built=true;
  const bar=mk('div',{id:'mBar'});
  bar.innerHTML='<div class="mb-l"><div class="mb-t">NIFTY 50</div><div id="mbSpot"></div><div class="mb-pills" id="mbPills"></div></div><div class="mb-m" id="mbPnl"></div><div id="mbBell"></div>';
  move($$('spotbig'),$$('mbSpot'));move($$('pfeed'),$$('mbPills'));move($$('pst'),$$('mbPills'));move($$('pnlbig'),$$('mbPnl'));move($$('bsnd'),$$('mbBell'));
  const tabs=mk('nav',{id:'mTabs'});
  TABS.forEach(([v,t])=>{const b=document.createElement('button');b.dataset.v=v;b.innerHTML=IC[v]+'<span>'+t+'</span>';
   b.onclick=()=>v==='more'?openSheet('mSheetMore'):go(v);tabs.appendChild(b);});
  const fab=mk('button',{id:'mFab',title:'Trade'});fab.innerHTML=IC.bolt+'Trade';fab.onclick=()=>openSheet('mSheetTrade');
  const back=mk('div',{id:'mBack'});back.onclick=closeSheets;
  const st=mk('div',{id:'mSheetTrade',className:'msheet'});
  st.innerHTML='<div class="grab"></div><h3>Trade</h3><div class="msub">Paper trades. Pick the expiry, then tap a button.</div>';
  const ctrl=document.querySelector('.ctrl');move(ctrl,st);
  if(ctrl&&!ctrl.querySelector('.sqnote')){const n=document.createElement('div');n.className='sqnote';n.textContent='Square off ALL asks for a second tap to confirm.';ctrl.appendChild(n);made.push(n);}
  ctrl&&ctrl.querySelectorAll('button').forEach(b=>{if(b.id!=='bsq'&&b.id!=='bauto')b.addEventListener('click',()=>setTimeout(closeSheets,350));});
  const sm=mk('div',{id:'mSheetMore',className:'msheet'});
  sm.innerHTML='<div class="grab"></div><div class="mlist"></div><h4 style="margin:4px 0 8px">Account</h4><div class="macct" id="mAcct"></div>';
  const list=sm.querySelector('.mlist');
  MORE.forEach(([v,i,t])=>{const b=document.createElement('button');b.dataset.v=v;b.innerHTML='<span class="ic">'+i+'</span>'+t+'<span class="ar">›</span>';b.onclick=()=>go(v);list.appendChild(b);});
  move($$('eqbig'),$$('mAcct'));move($$('sess'),$$('mAcct'));move($$('exp'),$$('mAcct'));move($$('clock'),$$('mAcct'));
  let y0=null;[st,sm].forEach(s=>{s.addEventListener('touchstart',e=>{y0=s.scrollTop<=0?e.touches[0].clientY:null;},{passive:true});
   s.addEventListener('touchend',e=>{if(y0!=null&&e.changedTouches[0].clientY-y0>70)closeSheets();y0=null;},{passive:true});});
  const bar2=document.querySelector('.chartbar');
  if(bar2&&!$$('mTradeBtn')){const t=document.createElement('button');t.id='mTradeBtn';t.innerHTML=IC.bolt+'Trade';t.onclick=()=>openSheet('mSheetTrade');bar2.appendChild(t);made.push(t);}
  if(bar2&&!$$('mDraw')){const d=document.createElement('button');d.id='mDraw';d.className='q';d.textContent='✏️ Draw';d.onclick=()=>{document.body.classList.toggle('mdraw');setTimeout(fit,30);};bar2.appendChild(d);made.push(d);}
  const x=mk('button',{id:'mFullX',textContent:'✕ Exit full screen'});x.onclick=()=>{document.body.classList.remove('full');setTimeout(fit,30);};
  syncTabs();}
 function unbuild(){if(!built)return;closeSheets();moved.reverse().forEach(([el,p,n])=>{try{p.insertBefore(el,n&&n.parentNode===p?n:null);}catch(e){}});moved=[];
  made.forEach(e=>e.remove());made=[];built=false;document.body.classList.remove('mdraw');}
 function tune(){if(!M())return;try{chart.applyOptions({layout:{fontSize:10},timeScale:{rightOffset:4,barSpacing:7,minBarSpacing:1.5},rightPriceScale:{scaleMargins:{top:.08,bottom:.08}},
  handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},handleScale:{pinch:true,axisPressedMouseMove:true},kineticScroll:{touch:true,mouse:false}});}catch(e){}}
 function lastBars(){if(!M())return;try{const n=(typeof bars!=='undefined'&&bars)?bars.length:0;if(n)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,n-60),to:n+4});}catch(e){}}
 function fit(){const L=$$('vChart');if(!L)return;const v=document.body.dataset.view;
  if(!M()||(v!=='chart'&&v!=='split')){L.style.height='';return;}
  if(document.body.classList.contains('full')){L.style.height=window.innerHeight+'px';return;}
  const tabH=($$('mTabs')||{}).offsetHeight||60, barH=($$('mBar')||{}).offsetHeight||58;
  const avail=window.innerHeight-barH-tabH;
  L.style.height=Math.max(220,v==='chart'?avail:Math.round(avail*0.62))+'px';}
 function apply(){if(M()){document.body.classList.add('m');build();tune();}else{document.body.classList.remove('m');unbuild();}syncTabs();fit();}
 try{const _sv=setView;setView=function(){const r=_sv.apply(this,arguments);syncTabs();setTimeout(fit,30);return r;};}catch(e){}
 try{const _lf=loadFull;loadFull=async function(){const r=await _lf.apply(this,arguments);lastBars();return r;};}catch(e){}
 try{const bl=$$('bLive');if(bl){const o=bl.onclick;bl.onclick=function(ev){if(o)o.call(this,ev);setTimeout(lastBars,50);};}}catch(e){}
 try{const bf=$$('bFull');if(bf){const o=bf.onclick;bf.onclick=function(ev){if(o)o.call(this,ev);setTimeout(fit,30);};}}catch(e){}
 new MutationObserver(()=>{syncTabs();setTimeout(fit,30);}).observe(document.body,{attributes:true,attributeFilter:['class','data-view']});
 window.addEventListener('resize',()=>{apply();});
 apply();
 if(M()&&document.body.dataset.view==='split'){try{setView('chart');}catch(e){}}
 setTimeout(()=>{lastBars();fit();},1200);
})();
"""


def inject(html):
    try:
        out = html
        a = '<meta charset="utf-8">'
        if out.count(a) != 1: raise ValueError("charset meta not found once")
        out = out.replace(a, a + VIEWPORT)
        b = "</style></head><body>"
        if out.count(b) != 1: raise ValueError("end of head not found once")
        out = out.replace(b, CSS + "</style></head><body>")
        c = "</script></body></html>"
        if out.count(c) != 1: raise ValueError("end of script not found once")
        out = out.replace(c, JS + c)
        return out
    except Exception as e:
        print("[mobile] mobile layout NOT added:", e, flush=True)
        return html
