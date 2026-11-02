"""
MOBILE LAYOUT - makes the dashboard usable on a phone. Only affects screens narrower than 820 px; the desktop view is unchanged.
Hook in nify_1.py:  HTML = mobile.inject(HTML)   (if an anchor is missing the page is left exactly as it was)
"""
VIEWPORT = '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0a0e14"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">'

CSS = r"""
#mFullX{display:none}
@media (max-width:820px),(max-height:520px) and (pointer:coarse){
 html,body{height:auto!important;min-height:100%;overflow-x:hidden!important;overflow-y:auto!important;-webkit-text-size-adjust:100%}
 body{display:block!important;font-size:14px}
 button,select,input{font-size:15px;min-height:36px}
 input,select,textarea{font-size:16px!important}               /* stops the phone zooming in when you tap a box */
 .top{padding:8px 10px!important;gap:8px!important;position:sticky;top:0;z-index:50}
 .top>b{font-size:14px!important;flex:0 1 auto}
 #spotbig:empty,#pnlbig:empty,#exp:empty{display:none}
 #spotbig,#pnlbig{font-size:17px!important}
 .nav{order:20;flex:1 1 100%;display:flex!important;flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
 .nav::-webkit-scrollbar{display:none}
 .nav button{flex:none;white-space:nowrap;padding:8px 12px!important}
 .ctrl{padding:8px 10px!important;gap:6px!important;flex-wrap:nowrap!important;overflow-x:auto;-webkit-overflow-scrolling:touch}
 .ctrl>*{flex:none}
 #sline{white-space:nowrap}
 .main{display:block!important}
 .left{height:68vh;min-height:360px}
 .chartbar{flex-wrap:nowrap!important;overflow-x:auto;-webkit-overflow-scrolling:touch;padding:6px 8px!important}
 .chartbar>*{flex:none}
 .cwrap{flex-direction:column}
 .tools{flex-direction:row!important;width:auto!important;height:auto;overflow-x:auto;padding:4px 6px!important;border-right:0!important;border-bottom:1px solid var(--line)}
 .tools>*{flex:none}
 #subs{max-height:none}
 .right{border-left:0!important;border-top:1px solid var(--line);padding:10px!important;overflow:visible!important}
 .page{padding:12px 10px!important;overflow:visible!important}
 .cards,#cards,#nets,#acctc,#tstats,#sigTop{grid-template-columns:1fr 1fr!important}
 .card .v{font-size:17px!important}
 #sigCards{grid-template-columns:1fr!important}
 table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:nowrap;max-width:100%}
 #vSettings .set,.set{grid-template-columns:1fr!important}
 .srow{grid-template-columns:1fr!important}
 #settings{max-width:100%}
 #indDlg{width:96vw!important;max-height:90vh}
 #indDlg .ibody{grid-template-columns:1fr!important;overflow-y:auto}
 #indlist{border-right:0!important;max-height:35vh}
 #indq{width:100%!important}
 #help{min-width:0!important;width:92vw}
 #trigform{left:4vw!important;width:92vw!important}
 #toasts{left:10px;right:10px;bottom:10px}
 .toast{max-width:none}
 #labBox [style*="max-width"]{max-width:100%!important}
 a.btn{margin:4px 6px 4px 0}
}
@media (max-width:420px){
 .cards,#cards,#nets,#acctc,#tstats{gap:5px!important}
 .card{padding:8px 9px!important}
 .card .v{font-size:15px!important}
 .card .l,.card .s{font-size:10.5px!important}
}

@media (max-width:820px),(max-height:520px) and (pointer:coarse){
 /* ---- chart on a phone ---- */
 body[data-view=chart] .top,body[data-view=split] .top{position:relative}
 body[data-view=chart] .left{height:calc(100svh - 120px);min-height:300px}
 body[data-view=split] .left{height:62svh;min-height:300px}
 body.full .left{height:100svh!important;min-height:0}
 body.full .main{height:100svh}
 .chartbar{gap:6px!important}
 .chartbar #tfSeg{order:-6}.chartbar #serSeg{order:-5}.chartbar #bLive{order:-4}.chartbar #bFull{order:-3}.chartbar #mDraw{order:-2}.chartbar #bInd{order:-1}
 .chartbar .seg button{padding:7px 10px!important}
 .chartbar button.q{padding:7px 10px!important}
 #bHelp,#hint{display:none!important}
 body:not(.mdraw) .tools,body:not(.mdraw) .chartbar label{display:none!important}
 body.mdraw #mDraw{background:var(--acc);color:#fff}
 #legend{font-size:11px!important;left:6px!important;top:4px!important;max-width:calc(100% - 64px);white-space:normal;line-height:1.35}
 .sub{height:105px!important}
 .phd{font-size:10.5px!important}
 #ovbar{flex-wrap:nowrap!important;overflow-x:auto}
 #ovbar>*{flex:none}
 #mFullX{display:none}
 body.full #mFullX{display:block;position:fixed;left:10px;bottom:46px;z-index:90;padding:8px 12px;border-radius:10px;background:#141d2bee}
}

"""

JS = r"""
/* ---------- mobile chart tweaks ---------- */
(function(){
 const M=()=>window.matchMedia('(max-width:820px),(max-height:520px) and (pointer:coarse)').matches;
 function tune(){ if(!M())return; try{chart.applyOptions({layout:{fontSize:10},timeScale:{rightOffset:4,barSpacing:7,minBarSpacing:1.5},rightPriceScale:{scaleMargins:{top:.08,bottom:.08}},handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},handleScale:{pinch:true,axisPressedMouseMove:true},kineticScroll:{touch:true,mouse:false}});}catch(e){} }
 function lastBars(){ if(!M())return; try{const n=(typeof bars!=='undefined'&&bars)?bars.length:0; if(n) chart.timeScale().setVisibleLogicalRange({from:Math.max(0,n-60),to:n+4});}catch(e){} }
 try{ const _lf=loadFull; loadFull=async function(){ const r=await _lf.apply(this,arguments); lastBars(); return r; }; }catch(e){}
 try{ const bl=document.getElementById('bLive'); if(bl){ const o=bl.onclick; bl.onclick=function(ev){ if(o)o.call(this,ev); setTimeout(lastBars,50); }; } }catch(e){}
 function addButtons(){
  const bar=document.querySelector('.chartbar'); if(!bar||document.getElementById('mDraw'))return;
  const d=document.createElement('button'); d.id='mDraw'; d.className='q'; d.textContent='✏️ Draw'; d.title='Show drawing tools';
  d.onclick=()=>{document.body.classList.toggle('mdraw');};
  bar.appendChild(d);
  const x=document.createElement('button'); x.id='mFullX'; x.textContent='✕ Exit full screen'; x.onclick=()=>{document.body.classList.remove('full'); setTimeout(fit,30);}; document.body.appendChild(x);
  if(!M()) d.style.display='none';
 }

 function fit(){ const L=document.getElementById('vChart'); if(!L)return; if(!M()){L.style.height='';return;}
  const v=document.body.dataset.view; if(v!=='chart'&&v!=='split'){L.style.height='';return;}
  if(document.body.classList.contains('full')){L.style.height=window.innerHeight+'px';return;}
  if(window.innerWidth>window.innerHeight){L.style.height=window.innerHeight+'px';return;}   /* phone on its side: chart fills the screen, scroll up for the buttons */
  const top=L.getBoundingClientRect().top+window.scrollY; const avail=window.innerHeight-top;
  L.style.height=Math.max(260, v==='chart'? avail : Math.round(window.innerHeight*0.6))+'px'; }
 try{ const _sv=setView; setView=function(){ const r=_sv.apply(this,arguments); setTimeout(fit,30); return r; }; }catch(e){}
 try{ const bf=document.getElementById('bFull'); if(bf){ const o=bf.onclick; bf.onclick=function(ev){ if(o)o.call(this,ev); setTimeout(fit,30); }; } }catch(e){}
 new MutationObserver(()=>setTimeout(fit,30)).observe(document.body,{attributes:true,attributeFilter:['class','data-view']});
 addButtons(); tune(); fit(); setTimeout(lastBars,1500); setTimeout(fit,600);
 window.addEventListener('resize',()=>{tune(); fit(); const d=document.getElementById('mDraw'); if(d) d.style.display=M()?'':'none';});
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
