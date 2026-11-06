"""The Swing tab of the dashboard (CSS, page and script). Used by swinglab.inject()."""

CSS = r"""
body[data-view=swing] .main{grid-template-columns:1fr}body[data-view=swing] #vPanel,body[data-view=swing] #vChart{display:none}body[data-view=swing] #vSwing{display:block}
#vSwing .swtabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 14px}
#vSwing .swtabs button{background:#151d29;border:1px solid #263244;color:#c9d4e3;border-radius:999px;padding:7px 13px;font-size:13px;display:flex;gap:7px;align-items:center}
#vSwing .swtabs button.act{background:#1d2a3d;border-color:#4f8ff7;color:#fff}
#vSwing .swtabs .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
#vSwing .swtabs .pill{font-size:11px;padding:1px 6px;border-radius:999px;background:#0d131c}
#vSwing .swgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:12px;margin:12px 0}
#vSwing .swbox{background:#111821;border:1px solid #223047;border-radius:10px;padding:12px 14px;min-width:0}
#vSwing .swbox h4{margin:0 0 8px;font-size:14px}
#vSwing .swnote{color:var(--mut);font-size:12.5px;line-height:1.5}
#vSwing .swchart{width:100%;height:auto;display:block}
#vSwing table{width:100%;border-collapse:collapse;font-size:12.5px}
#vSwing th{color:var(--mut);font-weight:500;text-align:right;padding:5px 6px;border-bottom:1px solid #223047;white-space:nowrap}
#vSwing td{text-align:right;padding:5px 6px;border-bottom:1px solid #1a2433;white-space:nowrap}
#vSwing th:first-child,#vSwing td:first-child{text-align:left}
#vSwing table.wrap td{white-space:normal}
#vSwing .tw{overflow-x:auto;max-height:420px;overflow-y:auto}
#vSwing .tag{font-size:11px;padding:2px 7px;border-radius:999px;background:#1b2433;color:#c9d4e3;white-space:nowrap}
#vSwing .tag.ok{background:#12352f;color:#4dd0b5}#vSwing .tag.bad{background:#3a1d22;color:#ff8a80}#vSwing .tag.wait{background:#2e2a16;color:#ffd54f}
#vSwing .pos{border:1px solid #263244;border-radius:9px;padding:10px 12px;margin:0 0 10px;background:#0f151e}
#vSwing .pos .swtop{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
#vSwing .plan{border-bottom:1px solid #1a2433;padding:7px 0;display:grid;grid-template-columns:1fr auto;gap:4px 10px;align-items:start}#vSwing .plan .why{grid-column:1/-1;color:var(--mut);font-size:12.5px;line-height:1.45}
#vSwing .bar{height:8px;background:#1a2330;border-radius:5px;position:relative;margin:8px 0 4px}
#vSwing .mbar{display:inline-block;height:8px;border-radius:3px;vertical-align:middle}
#vSwing .ev{font-size:12px;color:#aeb9c8;border-bottom:1px solid #18212e;padding:4px 0}
#vSwing .ev.trade{color:#e3ebf5}#vSwing .ev.error{color:#ff8a80}#vSwing .ev.warn{color:#ffd54f}
#vSwing .cards{grid-template-columns:repeat(4,1fr)}
@media (max-width:820px){#vSwing .cards{grid-template-columns:1fr 1fr!important}#vSwing .swtabs{flex-wrap:nowrap;overflow-x:auto}#vSwing .swtabs button{flex:0 0 auto}}
"""

PAGE = '<div class="page" id="vSwing"><div id="swHead"></div><div class="swtabs" id="swTabs"></div><div id="swBox"><p style="color:var(--mut)">Loading the Swing lab…</p></div></div>\n'

JS = r"""
/* ---------- Swing lab ---------- */
let swSub=(()=>{try{return localStorage.getItem('swSub')||'overview';}catch(e){return 'overview';}})(), swLast=null;
const SWC={acd:'#ffb74d',c:'#4f8ff7',cplus:'#26a69a',c_acd:'#ab47bc',cplus_acd:'#ef5350',target8:'#26c6da',core:'#9ccc65',p2ac:'#f06292',p12ac:'#7986cb',combo:'#ffa726',mix:'#ffd54f'};
const swInr=v=>{if(v==null||isNaN(v))return '–';const a=Math.abs(v),s=v<0?'−':'';return a>=1e7?s+'₹'+(a/1e7).toFixed(2)+' Cr':a>=1e5?s+'₹'+(a/1e5).toFixed(2)+' L':s+'₹'+Math.round(a).toLocaleString('en-IN');};
const swSgn=v=>v==null||isNaN(v)?'–':(v>0?'+':'')+swInr(v).replace('−','−');
const swPct=(v,d=1)=>v==null||isNaN(v)?'–':(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(d)+'%';
const swC=v=>v>0?'up':v<0?'dn':'';
const swEsc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function swLoad(){try{const d=await (await fetch('/api/swing/state')).json();swLast=d;swRender(d);}catch(e){$('swBox').innerHTML='<p style="color:#ef5350">Could not load the Swing lab: '+e+'</p>';}}
function swCmd(o){return post('/api/swing/cmd',o).then(()=>swLoad());}
function swGo(s){swSub=s;try{localStorage.setItem('swSub',s);}catch(e){}if(swLast)swRender(swLast);}
setInterval(()=>{if(view==='swing')swLoad();},2000);

function swChart(series,h=240){
 const all=[...new Set(series.flatMap(s=>s.pts.map(p=>p[0])))].sort();
 if(all.length<2)return '<div class="swnote" style="padding:30px 0;text-align:center">The chart starts after the first full trading day.</div>';
 const sm=window.innerWidth<700,W=sm?560:900,L=sm?62:70,R=sm?126:118,T=12,B=26,ix=Object.fromEntries(all.map((d,i)=>[d,i]));
 const vals=series.flatMap(s=>s.pts.map(p=>p[1])).concat(series.map(s=>s.base).filter(v=>v!=null));
 let lo=Math.min(...vals),hi=Math.max(...vals);if(hi-lo<1){hi+=1000;lo-=1000;}const pad=(hi-lo)*.08;lo-=pad;hi+=pad;
 const x=i=>L+(W-L-R)*i/(all.length-1),y=v=>T+(h-T-B)*(1-(v-lo)/(hi-lo));
 let g='';for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=y(v);g+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#1d2735"/><text x="${L-8}" y="${yy+4}" text-anchor="end" fill="#7d8a9c" font-size="11">${swInr(v)}</text>`;}
 const t0=all[0],t1=all[all.length-1];g+=`<text x="${L}" y="${h-6}" fill="#7d8a9c" font-size="11">${t0}</text><text x="${W-R}" y="${h-6}" text-anchor="end" fill="#7d8a9c" font-size="11">${t1}</text>`;
 const base=series.find(s=>s.base!=null);if(base)g+=`<line x1="${L}" x2="${W-R}" y1="${y(base.base)}" y2="${y(base.base)}" stroke="#56627a" stroke-dasharray="4 4"/>`;
 const ends=[];
 series.forEach(s=>{if(!s.pts.length)return;const d=s.pts.map((p,i)=>(i?'L':'M')+x(ix[p[0]]).toFixed(1)+' '+y(p[1]).toFixed(1)).join(' ');
  g+=`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;const lp=s.pts[s.pts.length-1];ends.push({y:y(lp[1]),v:lp[1],s});});
 ends.sort((a,b)=>a.y-b.y);for(let i=1;i<ends.length;i++)if(ends[i].y-ends[i-1].y<26)ends[i].y=ends[i-1].y+26;
 ends.forEach(e=>{g+=`<circle cx="${x(all.length-1)}" cy="${y(e.v)}" r="3.5" fill="${e.s.color}"/><text x="${W-R+8}" y="${e.y}" fill="${e.s.color}" font-size="11.5" font-weight="600">${swEsc(e.s.name)}</text><text x="${W-R+8}" y="${e.y+13}" fill="#c9d4e3" font-size="11">${swInr(e.v)}</text>`;});
 return `<svg class="swchart" viewBox="0 0 ${W} ${h}" role="img" aria-label="Account value over time">${g}</svg>`;}

function swRender(d){
 if(!d.ready){$('swBox').innerHTML='<p style="color:var(--mut)">The Swing lab is starting…</p>';return;}
 $('swHead').innerHTML='<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0"><h3 style="margin:0">🌊 Swing lab</h3><button class="'+(d.enabled?'green':'red')+'" onclick="swCmd({cmd:\'enable\',on:'+(!d.enabled)+'})">'+(d.enabled?'● AUTO-TRADING ON — click to pause':'○ PAUSED — click to start')+'</button>'
  +'<span class="swnote">Paper money only. Five separate accounts of '+swInr(d.cfg.start_cash)+' each trade the researched rules by themselves. Daily data: '+(d.data.daily_last?'up to '+d.data.daily_last:'<b style="color:#ffd54f">not loaded yet</b>')+(d.data.holidays_ok?'':' · holiday list unavailable (weekends only)')+'</span></div>';
 const tabs=[['overview','Overview',null]].concat(d.accts.map(a=>[a.id,a.name,a]));
 $('swTabs').innerHTML=tabs.map(([k,n,a])=>'<button class="'+(swSub===k?'act':'')+'" onclick="swGo(\''+k+'\')">'+(a?'<span class="dot" style="background:'+SWC[k]+'"></span>':'')+swEsc(n)+(a?' <span class="pill '+swC(a.ret)+'">'+swPct(a.ret)+'</span>':'')+'</button>').join('');
 const a=d.accts.find(x=>x.id===swSub);
 (typeof keepView==='function'?keepView($('swBox'),a?swAcct(d,a):swOverview(d)):($('swBox').innerHTML=a?swAcct(d,a):swOverview(d)));}

function swPlanBox(d,kinds){
 const p=d.plan,pv=d.preview,fl=p&&p.flags,ent=e=>'<div class="plan"><div><b>'+swEsc(e.rule)+'</b> '+swEsc({P1:'Pullback in a strong trend',P2:'Bounce in a correction',P3:'Turn of the month',L1:'20-day trade (Core only)',Q:'Put spread after a dip (Core only)'}[e.rule])+' <span class="swnote">· '+(e.rule==='Q'?'held to the '+swEsc(e.exit_due)+' expiry':'exit at the open on '+swEsc(e.exit_due))+'</span></div><div>'+(e.skip?'<span class="tag bad">skip: '+swEsc(e.skip)+'</span>':'<span class="tag ok">trade</span>')+'</div><div class="why">'+swEsc(e.why)+'</div></div>';
 let h='<div class="swbox"><h4>Swing signals</h4>';
 if(p){h+='<div class="swnote" style="margin-bottom:6px">Today, '+swEsc(p.day)+(p.note?' · '+swEsc(p.note):'')+'</div>'+(p.entries.length?p.entries.map(ent).join(''):'<div class="swnote">No swing entry today.</div>');
  if(fl)h+='<div class="swnote" style="margin-top:8px">Last close '+swEsc(fl.day)+': 10-day move '+swPct(fl.ret10,2)+' (rank '+(fl.ret10_rank==null?'–':fl.ret10_rank)+', P1 needs above 0.80) · close at '+(fl.close_pos==null?'–':Math.round(fl.close_pos*100))+'% of the day\'s range (P1 needs under 20%) · '+(fl.below_52w_high==null?'–':fl.below_52w_high.toFixed(1))+'% below the 52-week high (P2 needs 10%+, Q needs 5%+) · RSI(14) '+(fl.rsi14==null?'–':fl.rsi14)+' (L1 needs above 70 with a close in the bottom 20%) · gap '+swPct(fl.gap,2)+(fl.up_day?' · up day':' · down day')+'</div>';
  if(p.q_check)h+='<div class="swnote">Q: '+swEsc(p.q_check)+'</div>';
  if(p.p3_next)h+='<div class="swnote">Next turn-of-the-month entry: <b>'+swEsc(p.p3_next)+'</b> at the open.</div>';}
 else h+='<div class="swnote">Waiting for the daily data (loads after 08:35 on trading days).</div>';
 if(pv)h+='<div style="margin-top:10px"><b>Tomorrow ('+swEsc(pv.day)+')</b> <span class="swnote">preview from today\'s live candle; confirmed with official data next morning</span>'+(pv.entries.length?pv.entries.map(ent).join(''):'<div class="swnote">No swing entry expected.</div>')+'</div>';
 return h+'</div>';}

function swAcdBox(d){
 const A=d.acd;let h='<div class="swbox"><h4>A/C/D today</h4>';
 if(!A)return h+'<div class="swnote">Starts at 09:15 on trading days.</div></div>';
 const st=(x,txt)=>x==null?'<span class="tag wait">waiting</span> '+txt:(x.side?'<span class="tag ok">'+(x.side>0?'BUY':'SELL')+'</span> ':'<span class="tag">no</span> ')+txt;
 h+='<div class="swnote" style="margin-bottom:8px"><b style="color:#e3ebf5">'+swEsc(A.status)+'</b></div><table class="wrap">'
  +'<tr><td>Gap at the open</td><td>'+swPct(A.gap,2)+'</td></tr>'
  +'<tr><td>A · gap + opening drive (09:45)</td><td>'+st(A.A,A.A?'move '+swPct(A.A.move,2)+' (needs ±0.30% with the gap)':'')+'</td></tr>'
  +'<tr><td>C · breakout after an NR7 day</td><td>'+(A.prev_nr7?st(A.C,A.C?'5-min close beyond '+Math.round(A.C.lo)+'–'+Math.round(A.C.hi):'yesterday was NR7: watching the 09:15–09:30 range'):'<span class="tag">off</span> yesterday was not an NR7 day')+'</td></tr>'
  +'<tr><td>D · wild-open fade (10:00)</td><td>'+st(A.D,A.D?'first-30-min range '+(A.D.r30==null?'–':A.D.r30.toFixed(2))+'% vs usual '+(A.D.avg==null?'–':A.D.avg.toFixed(2))+'% (needs 1.5×)':'')+'</td></tr></table>';
 if(A.fired)h+='<div class="swnote" style="margin-top:6px">First signal: <b>'+swEsc(A.fired.rule)+'</b> at '+swEsc(A.fired.at)+' → '+(A.fired.side>0?'call':'put')+'. One A/C/D trade a day.</div>';
 return h+'</div>';}

function swOverview(d){
 let h='<div class="swbox"><h4>All accounts</h4>'+swChart(d.accts.map(a=>({name:a.name,color:SWC[a.id],pts:a.hist.map(x=>[x[0],x[2]]),base:a.start})),260)+'</div>';
 h+='<div class="swbox" style="margin-top:12px"><div class="tw"><table><tr><th>Account</th><th>Value now</th><th>Return</th><th>This month</th><th>Today</th><th>Open</th><th>Trades</th><th>Won</th><th>Worst fall</th><th>Backtest a month</th><th>Backtest worst fall</th></tr>'
  +d.accts.map(a=>'<tr style="cursor:pointer" onclick="swGo(\''+a.id+'\')"><td><span class="mbar" style="width:10px;background:'+SWC[a.id]+'"></span> <b>'+swEsc(a.name)+'</b></td><td>'+swInr(a.mtm)+'</td><td class="'+swC(a.ret)+'">'+swPct(a.ret)+'</td><td class="'+swC(a.month_ret)+'">'+swPct(a.month_ret)+'</td><td class="'+swC(a.today)+'">'+swSgn(a.today)+'</td><td>'+a.open.length+'</td><td>'+a.n+'</td><td>'+(a.win_rate==null?'–':a.win_rate+'%')+'</td><td class="dn">'+swPct(a.maxdd)+'</td><td>'+(a.backtest.month==null?'–':'+'+a.backtest.month+'%')+'</td><td>'+(a.backtest.dd==null?'–':a.backtest.dd+'%')+'</td></tr>').join('')+'</table></div>'
  +'<div class="swnote" style="margin-top:6px">Backtest figures are from the research and are not a promise. Click a row to open that account.</div></div>';
 h+='<div class="swgrid">'+swPlanBox(d)+swAcdBox(d)+'</div>';
 h+=swFillBox(d);
 h+=swRules(d);
 h+='<div class="swbox"><h4>What the lab did</h4>'+(d.events.length?d.events.map(e=>'<div class="ev '+e.kind+'">'+swEsc(e.t.slice(5,16))+' · '+swEsc(e.msg)+'</div>').join(''):'<div class="swnote">Nothing yet.</div>')+'</div>';
 h+='<p style="margin-top:12px"><a class="btn" href="/api/swing/trades.csv" download>Download trades.csv</a> <a class="btn" href="/api/swing/equity.csv" download>Download equity.csv</a> <a class="btn" href="/api/swing/fills.csv" download>fills.csv</a> <a class="btn" href="/api/swing/signals.csv" download>signals.csv</a> <button class="red" onclick="if(confirm(\'Archive all Swing lab data and restart all accounts?\'))swCmd({cmd:\'reset\'})">Reset all accounts</button></p>';
 if(d.err)h+='<p style="color:#ef5350">Last error: '+swEsc(d.err)+'</p>';
 return h;}

function swFillBox(d){
 const f=d.fillq; if(!f)return '';
 const nm={swing:'Swing spreads (C, C+, L1)',credit:'Put spread (Q)',acd:'A/C/D intraday'};
 let h='<div class="swbox" style="margin-bottom:12px"><h4>Fill quality</h4><div class="swnote">Every paper fill is taken at the live ask (buying) or bid (selling). Here is what that cost per leg, against what the research assumed. "Bigger than shown" = our quantity was larger than the size on offer at that price, so a real order would have filled worse.</div>';
 h+=f.by_kind.length?'<div class="tw"><table><tr><th>Trades</th><th>Fills</th><th>Legs</th><th>Half the bid-ask gap</th><th>Paid vs mid-price</th><th>Research assumed</th><th>Bigger than shown</th></tr>'
  +f.by_kind.map(x=>'<tr><td style="text-align:left">'+swEsc(nm[x.kind]||x.kind)+'</td><td>'+x.fills+'</td><td>'+x.legs+'</td><td>'+(x.half_spread==null?'–':x.half_spread+' pts')+'</td><td class="'+(x.paid_vs_mid!=null&&x.assumed!=null&&x.paid_vs_mid>x.assumed?'dn':'up')+'"><b>'+(x.paid_vs_mid==null?'–':x.paid_vs_mid+' pts')+'</b></td><td>'+(x.assumed==null?'–':x.assumed+' pts')+'</td><td>'+(x.too_big_pct==null?'–':x.too_big_pct+'%')+'</td></tr>').join('')+'</table></div>':'<div class="swnote">No fills yet.</div>';
 h+='<div class="swnote" style="margin-top:8px"><b>Your real fills</b>: '+(f.real.n?f.real.n+' logged; on average your real price was <b>'+f.real.avg_worse+' pts '+(f.real.avg_worse>0?'worse':'better')+'</b> than the paper fill.':'none logged yet.')+' When you trade a signal for real, log your price:</div>'
  +'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px"><input id="rfI" placeholder="trade id (e.g. S20261106-001)" style="width:190px"><select id="rfS"><option value="entry">entry</option><option value="exit">exit</option></select><input id="rfL" type="number" min="0" value="0" title="leg number (0 = first leg)" style="width:60px"><input id="rfP" type="number" step="0.05" placeholder="your price" style="width:110px"><button onclick="swCmd({cmd:\'realfill\',intent:document.getElementById(\'rfI\').value.trim(),stage:document.getElementById(\'rfS\').value,leg:+document.getElementById(\'rfL\').value,price:+document.getElementById(\'rfP\').value})">Log real fill</button> <a class="btn" href="/api/swing/realfills.csv" download>realfills.csv</a></div>';
 return h+'</div>';}

function swRules(d){const c=d.cfg;
 return '<details class="swbox" style="margin-bottom:12px"><summary style="cursor:pointer"><b>The rules (frozen from the research)</b></summary><div class="swnote" style="margin-top:8px">'
 +'<p><b>Swing signals</b>, decided after the close and traded at the next open (09:16): <b>P1</b> pullback in a strong trend: the 10-day rise is in the top 20% of the last 2 years and Nifty closes in the bottom 20% of the day\'s range → hold 1 day. <b>P2</b> bounce in a correction: Nifty is 10%+ below its 52-week high and opened 0.3%+ higher but closed lower → hold 3 days. <b>P3</b> turn of the month: buy on the 4th-last trading day, sell at the open of the 4th trading day of the next month. Every swing trade exits early if Nifty falls '+c.stop_pct+'% below its entry level.</p>'
 +'<p><b>Option C</b> buys the call '+c.spread_pts+' pts below spot and sells the call '+c.spread_pts+' above, in the monthly expiry at least 7 days after the exit. <b>Option C+</b> uses a weekly spread (expiry at least 2 days after the exit) for P1 and P2, and the monthly one for P3. Size: about '+Math.round(c.size_frac*100)+'% of the account as premium, at least 1 lot when one lot is under '+Math.round(c.size_max_frac*100)+'% of it; '+swInr(c.short_margin)+' a lot is set aside as margin for the sold leg.</p>'
 +'<p><b>A/C/D</b>, at most one trade a day, the first signal wins: <b>A</b> gap + opening drive: at 09:45 Nifty is 0.3%+ beyond the open in the direction of the gap. <b>C</b> only on the day after an NR7 day (narrowest range of 7): the first 5-minute close beyond the 09:15–09:30 range. <b>D</b> wild-open fade: the 09:15–09:45 range is 1.5× its 20-day average; at 10:00 trade against the side of the morning\'s average price. Buys a weekly option ~'+c.acd_itm+' pts in the money (call to buy, put to sell); 1 lot per '+swInr(c.acd_per_lot)+'. A and C exit on a trailing stop of 2× the 5-minute range or at 15:20; D exits after 60 minutes.</p>'
 +'<p><b>L1, the 20-day trade</b> (Core only): RSI(14) above 70 and Nifty closes in the bottom 20% of the day\'s range → the same monthly 300/300 call spread, held 20 trading days with <b>no</b> 2% stop (as tested).</p>'
 +'<p><b>Q, the put spread after a dip</b> (Core only): on the first trading day after each monthly expiry, if Nifty closed 5%+ below its 52-week high, sell the next monthly put ~6% below Nifty and buy the one ~9% below; held to expiry (closed at 15:15 on expiry day). 1 lot per '+swInr(d.q_per_lot||100000)+'; the full possible loss is set aside.</p>'
 +'<p><b>Core</b> = Option C+ + L1 + Q, 20% of the account per swing trade: the recommended book from the Nov 2026 report. <b>The split view</b> (default 70% Core + 30% 8% target) is not a separate account: it adds up X% of Core and the rest of the 8% target account from the day Core started. Change X on its page; the whole history is redrawn with the new split.</p>'
 +'<p><b>8% target</b> runs Option C+ and A/C/D together with 15% of the account in each trade (at least 1 lot while one lot is under 22.5% of it). Bigger bets: bigger months and deeper falls.</p>'
 +'<p>Fills: buys at the ask, sells at the bid, broker charges deducted. Idle cash earns '+Math.round(c.liquid_rate*100)+'% a year like a liquid fund (shown separately).</p></div></details>';}

function swAcct(d,a){
 const k=a.id,bt=a.backtest,swing=k!=='acd',acd=k==='acd'||k.endsWith('_acd')||k==='target8'||k==='mix'||k==='p2ac'||k==='p12ac'||k==='combo';
 let h='<div class="cards">'
  +card('<span class="mbar" style="width:9px;background:'+SWC[k]+'"></span> Account value',swInr(a.mtm),'started with '+swInr(a.start)+(a.unreal?' · open trades '+swSgn(a.unreal):''))
  +card('Return','<span class="'+swC(a.ret)+'">'+swPct(a.ret,2)+'</span>','this month '+swPct(a.month_ret))
  +card('Today','<span class="'+swC(a.today)+'">'+swSgn(a.today)+'</span>',a.today_n+' trade(s) closed today')
  +card('Free cash',swInr(a.cash),'interest earned '+swInr(a.interest))
  +card('Closed trades',a.n,(a.win_rate==null?'–':a.win_rate+'% won')+' · profit factor '+(a.pf==null?'–':a.pf))
  +card('Realised P&L','<span class="'+swC(a.realized)+'">'+swSgn(a.realized)+'</span>','after '+swInr(a.fees)+' charges')
  +card('Worst fall','<span class="dn">'+swPct(a.maxdd)+'</span>','from the highest value so far')
  +card('Backtest',bt.month==null?'not tested yet':'+'+bt.month+'% a month',swEsc(bt.period)+(bt.dd==null?'':' · worst fall '+bt.dd+'%'))+'</div>';
 if(a.virtual)h+=swMixBox(a);
 h+='<div class="swbox" style="margin-top:12px"><h4>Account value</h4>'+swChart([{name:a.name,color:SWC[k],pts:a.hist.map(x=>[x[0],x[2]]),base:a.start}],230)+'<div class="swnote">'+swEsc(bt.note)+'</div></div>';
 h+='<div class="swbox" style="margin-top:12px"><h4>Open trades ('+a.open.length+')</h4>'+(a.open.length?a.open.map(p=>swPos(p)).join(''):'<div class="swnote">None. '+(swing?'Swing trades open at 09:16 on signal days. ':'')+(acd?'A/C/D trades open between 09:35 and the afternoon when a rule fires.':'')+'</div>')+'</div>';
 h+='<div class="swgrid">'+(swing?swPlanBox(d):'')+(acd?swAcdBox(d):'')+'</div>';
 const mx=Math.max(1,...a.months.map(m=>Math.abs(m.ret||0)));
 h+='<div class="swbox"><h4>Month by month</h4>'+(a.months.length?'<div class="tw"><table><tr><th>Month</th><th>Trades</th><th>Won</th><th>Closed P&L</th><th>Value at month end</th><th>Return</th><th></th></tr>'
  +a.months.slice().reverse().map(m=>'<tr><td>'+m.month+'</td><td>'+m.trades+'</td><td>'+m.wins+'</td><td class="'+swC(m.pnl)+'">'+swSgn(m.pnl)+'</td><td>'+swInr(m.end)+'</td><td class="'+swC(m.ret)+'"><b>'+swPct(m.ret)+'</b></td><td style="text-align:left;width:120px"><span class="mbar" style="width:'+Math.round(Math.abs(m.ret||0)/mx*110)+'px;background:'+((m.ret||0)>=0?'#26a69a':'#ef5350')+'"></span></td></tr>').join('')+'</table></div>':'<div class="swnote">The first month appears after the first trading day.</div>')+'</div>';
 h+='<div class="swbox" style="margin-top:12px"><h4>Trades (latest first)</h4>'+(a.trades.length?'<div class="tw"><table><tr><th>Closed</th><th>Signal</th><th>What</th><th>Lots</th><th>Paid</th><th>Got</th><th>Nifty in → out</th><th>Why it closed</th><th>Charges</th><th>Net P&L</th></tr>'
  +a.trades.map(t=>'<tr><td>'+swEsc(t.exit_time.slice(5,16))+'<div class="swnote">'+swEsc(t.intent)+'</div></td><td><b>'+swEsc(t.rule)+'</b></td><td style="text-align:left">'+swEsc(t.label)+'</td><td>'+t.lots+'</td><td>'+(+t.entry_pts).toFixed(2)+'</td><td>'+(+t.exit_pts).toFixed(2)+'</td><td>'+Math.round(+t.entry_spot)+' → '+Math.round(+t.exit_spot)+'</td><td style="text-align:left">'+swEsc(t.reason)+'</td><td>'+swInr(+t.fees)+'</td><td class="'+swC(+t.net)+'"><b>'+swSgn(+t.net)+'</b></td></tr>').join('')+'</table></div>':'<div class="swnote">No closed trades yet.</div>')+'</div>';
 h+='<div style="margin-top:12px">'+swRules(d)+'</div>';
 return h;}

function swMixBox(a){
 const w=a.mix_core,bt=a.mix_bt||{};
 let h='<div class="swbox" style="margin-top:12px"><h4>Change the split</h4><div class="swnote">How much of the money follows <b>Core</b>; the rest follows the <b>8% target</b> account. This is only a view: no trades change, and the whole history is redrawn with the new split.</div>'
  +'<div style="display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap"><span>Core</span><input id="swMixR" type="range" min="0" max="100" step="5" value="'+w+'" oninput="document.getElementById(\'swMixL\').textContent=this.value+\'% Core + \'+(100-this.value)+\'% 8% target\'" style="flex:1;min-width:160px"><span>8% target</span>'
  +'<b id="swMixL">'+w+'% Core + '+(100-w)+'% 8% target</b><button onclick="swCmd({cmd:\'mix\',core:+document.getElementById(\'swMixR\').value})">Save split</button></div>'
  +'<div class="tw"><table><tr><th>Core share</th><th>Rs 1L became</th><th>A month</th><th>Median month</th><th>Worst month</th><th>Worst fall</th><th>Months up</th></tr>'
  +Object.keys(bt).map(Number).sort((x,y)=>y-x).map(k=>{const b=bt[k];return '<tr'+(Math.abs(k-w)<5?' style="background:rgba(255,213,79,.15)"':'')+'><td>'+k+'%</td><td>'+swInr(b[0])+'</td><td>+'+b[1]+'%</td><td>'+(b[2]>=0?'+':'')+b[2]+'%</td><td class="dn">'+b[3]+'%</td><td class="dn">'+b[4]+'%</td><td>'+b[5]+'/25</td></tr>';}).join('')
  +'</table></div><div class="swnote">Backtest on real prices, Oct 2024 – Oct 2026, month-end values, never rebalanced. Short history: treat as a guide, not a promise.</div></div>';
 return h;}

function swPos(p){
 let h='<div class="pos"><div class="swtop"><div><b>'+swEsc(p.rule)+'</b> '+swEsc(p.rule_name)+' · '+swEsc(p.label)+' · '+p.lots+' lot ('+p.qty+') <span class="swnote">· id '+swEsc(p.id)+'</span></div><div class="'+swC(p.unreal)+'" style="font-size:16px;font-weight:600">'+(p.unreal==null?'<span class="swnote">waiting for prices</span>':swSgn(p.unreal))+'</div></div>'
  +'<div class="swnote">Bought '+swEsc(p.t_in.slice(5,16))+' at '+(+p.entry).toFixed(2)+' pts · now '+(p.now==null?'–':(+p.now).toFixed(2))+' · money tied up '+swInr(p.blocked)+(p.status!=='OPEN'?' · <b>closing…</b>':'')+'</div>';
 if(p.kind==='swing'&&p.spot&&p.spot_in&&p.stop){
  const lo=p.stop,hi=p.spot_in*1.03,pct=Math.max(0,Math.min(1,(p.spot-lo)/(hi-lo))),pin=(p.spot_in-lo)/(hi-lo);
  h+='<div class="bar"><div style="position:absolute;left:0;top:0;height:8px;width:'+(pct*100)+'%;border-radius:5px;background:'+(p.spot>=p.spot_in?'#26a69a':'#ef9a9a')+'"></div><div style="position:absolute;left:'+(pin*100)+'%;top:-3px;width:2px;height:14px;background:#c9d4e3"></div></div>'
   +'<div class="swnote">Nifty '+Math.round(p.spot).toLocaleString('en-IN')+' · entry '+Math.round(p.spot_in).toLocaleString('en-IN')+' · stop '+Math.round(p.stop).toLocaleString('en-IN')+' · planned exit at the open on <b>'+swEsc(p.exit_due)+'</b></div>';}
 if(p.kind==='credit')h+='<div class="swnote">Credit spread: collected '+(-p.entry).toFixed(2)+' pts a unit; held to the <b>'+swEsc(p.exit_due)+'</b> expiry, no stop. Money set aside = the most it can lose.</div>';
 if(p.kind==='acd')h+='<div class="swnote">'+(p.exit_rule==='HOLD60'?'Exits at '+swEsc(p.hold_until)+' (60 minutes)':'Trailing stop at '+(p.stop_lvl==null?'–':(p.stop_lvl>0?'+':'')+p.stop_lvl)+' pts from entry · best so far '+(p.best==null?'–':'+'+p.best)+' pts · otherwise exits at 15:20')+'</div>';
 h+='<button class="red" style="margin-top:8px;padding:4px 10px" onclick="if(confirm(\'Close this trade now in every account that holds it?\'))swCmd({cmd:\'close\',id:\''+p.id+'\'})">Close now</button></div>';
 return h;}
"""
