"""Embedded static assets for generated registry sites (no external fonts/CDNs)."""

from __future__ import annotations

CSS = """
:root{--bg:#fff;--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--card:#f9fafb;--accent:#4f46e5;
--ok:#15803d;--okbg:#dcfce7;--warn:#a16207;--warnbg:#fef9c3;--bad:#b91c1c;--badbg:#fee2e2;
--info:#1d4ed8;--infobg:#dbeafe;--code:#f3f4f6}
:root[data-theme="dark"]{--bg:#0b1020;--fg:#e5e7eb;--muted:#9ca3af;--line:#252b3d;--card:#121830;
--accent:#818cf8;--ok:#4ade80;--okbg:#0f2a1c;--warn:#facc15;--warnbg:#2b2408;--bad:#f87171;--badbg:#301416;
--info:#93c5fd;--infobg:#111c3a;--code:#171e36}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0b1020;--fg:#e5e7eb;--muted:#9ca3af;
--line:#252b3d;--card:#121830;--accent:#818cf8;--ok:#4ade80;--okbg:#0f2a1c;--warn:#facc15;--warnbg:#2b2408;
--bad:#f87171;--badbg:#301416;--info:#93c5fd;--infobg:#111c3a;--code:#171e36}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header.top{display:flex;flex-wrap:wrap;gap:.75rem 1.25rem;align-items:center;padding:.75rem 1.25rem;
border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
header.top .brand{font-weight:700;color:var(--fg)}header.top nav{display:flex;flex-wrap:wrap;gap:.9rem;flex:1}
button{font:inherit;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.25rem .6rem;cursor:pointer}
main{max-width:1100px;margin:0 auto;padding:1.25rem}
h1{font-size:1.7rem;margin:.2rem 0 .5rem}h2{font-size:1.2rem;margin:1.6rem 0 .5rem;border-bottom:1px solid var(--line);padding-bottom:.25rem}
h3{font-size:1rem;margin:1rem 0 .3rem}
.muted{color:var(--muted)}.mono,code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.86em}
code{background:var(--code);padding:.05rem .3rem;border-radius:4px}
pre{background:var(--code);padding:.7rem .9rem;border-radius:8px;overflow:auto;max-height:26rem}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:.4rem 0 1rem}
th,td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.c,th.c{text-align:center}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin:.75rem 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.8rem 1rem}
.card .n{font-size:1.6rem;font-weight:700}.card .l{color:var(--muted);font-size:.82rem}
.badges{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0}
.badge{display:inline-block;font-size:.72rem;font-weight:600;padding:.1rem .5rem;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--muted)}
.badge.approved,.badge.stable,.badge.tested,.badge.signed,.badge.verified,.badge.no{background:var(--okbg);color:var(--ok);border-color:transparent}
.badge.deprecated,.badge.yanked,.badge.restricted{background:var(--warnbg);color:var(--warn);border-color:transparent}
.badge.quarantined{background:var(--badbg);color:var(--bad);border-color:transparent}
.badge.runtime,.badge.license,.badge.agent,.badge.kind{background:var(--infobg);color:var(--info);border-color:transparent}
.banner{padding:.7rem 1rem;border-radius:8px;margin:.8rem 0;border:1px solid var(--line);background:var(--card)}
.banner.warn{background:var(--warnbg);color:var(--warn)}.banner.bad{background:var(--badbg);color:var(--bad)}
.banner.info{background:var(--infobg);color:var(--info)}
.yes{color:var(--ok);font-weight:700}.no{color:var(--muted)}
.selector{display:flex;flex-wrap:wrap;gap:.5rem;margin:.6rem 0}.selector a{border:1px solid var(--line);border-radius:6px;padding:.15rem .6rem}
.selector a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.breaking{color:var(--bad);font-weight:700}.add{color:var(--ok)}.del{color:var(--bad)}
ul.plain{list-style:none;padding:0;margin:.3rem 0}ul.plain li{padding:.15rem 0}
.diagram{max-width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.5rem}
input[type=search]{font:inherit;padding:.4rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);min-width:16rem}
footer{max-width:1100px;margin:2rem auto;padding:1rem 1.25rem;color:var(--muted);font-size:.8rem;border-top:1px solid var(--line)}
.page[hidden]{display:none}.js-router .page{display:none}.js-router .page.active{display:block}
pre.diff .add{color:var(--ok)}pre.diff .del{color:var(--bad)}
@media (max-width:640px){main{padding:.75rem}input[type=search]{min-width:0;width:100%}}
@media print{header.top,footer,button,input{display:none!important}.js-router .page{display:block!important;page-break-after:always}
:root{--bg:#fff;--fg:#000}a{color:#000}}
"""

JS = r"""
(function(){
'use strict';
var root=document.documentElement;
try{var t=localStorage.getItem('ananke-theme');if(t){root.setAttribute('data-theme',t);}}catch(e){}
var toggle=document.getElementById('theme-toggle');
if(toggle){toggle.addEventListener('click',function(){
var cur=root.getAttribute('data-theme')||(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
var next=cur==='dark'?'light':'dark';root.setAttribute('data-theme',next);
try{localStorage.setItem('ananke-theme',next);}catch(e){}});}
document.querySelectorAll('input[data-filter]').forEach(function(input){
var table=document.getElementById(input.getAttribute('data-filter'));if(!table){return;}
input.addEventListener('input',function(){var q=input.value.toLowerCase();
table.querySelectorAll('tbody tr').forEach(function(tr){tr.hidden=q&&tr.textContent.toLowerCase().indexOf(q)<0;});});});
var dataEl=document.getElementById('search-data');
var box=document.getElementById('search-box');
var out=document.getElementById('search-results');
function tokens(q){return q.toLowerCase().split(/\s+/).filter(Boolean);}
function parse(q){var f={},terms=[];tokens(q).forEach(function(tok){var i=tok.indexOf(':');
if(i>0&&['kind','capability','runtime','trust','license','tag','channel','lifecycle'].indexOf(tok.slice(0,i))>=0){
(f[tok.slice(0,i)]=f[tok.slice(0,i)]||[]).push(tok.slice(i+1));}else{terms.push(tok);}});return {f:f,t:terms};}
function matches(e,p){
var hay=[e.name,e.summary,e.tags.join(' '),e.capabilities.join(' '),e.runtime.join(' ')].join(' ').toLowerCase();
for(var i=0;i<p.t.length;i++){if(hay.indexOf(p.t[i])<0){return false;}}
var checks={kind:e.kind,trust:e.trust,channel:e.channel,lifecycle:e.lifecycle,license:e.license.toLowerCase()};
for(var k in p.f){for(var j=0;j<p.f[k].length;j++){var v=p.f[k][j];
if(k==='capability'){if(!e.capabilities.some(function(c){return c===v||c.indexOf(v+'.')===0;})){return false;}}
else if(k==='runtime'){if(e.runtime.indexOf(v)<0){return false;}}
else if(k==='tag'){if(e.tags.indexOf(v)<0){return false;}}
else if(k==='license'){if(checks.license.indexOf(v)<0){return false;}}
else if(checks[k]!==v){return false;}}}
return true;}
if(dataEl&&box&&out){
var entries=[];try{entries=JSON.parse(dataEl.textContent||'[]');}catch(e){}
var base=out.getAttribute('data-base')||'';
function render(){var p=parse(box.value);out.textContent='';var n=0;
entries.forEach(function(e){if(n>=200||!matches(e,p)){return;}n++;
var li=document.createElement('li');var a=document.createElement('a');
a.href=e.href||(base+e.page);a.textContent=e.name+'@'+e.version;li.appendChild(a);
var s=document.createElement('span');s.className='muted';s.textContent='  '+e.kind+' · '+e.trust+' · '+e.summary;
li.appendChild(s);out.appendChild(li);});
if(!n){var li2=document.createElement('li');li2.className='muted';li2.textContent='No matches';out.appendChild(li2);}}
box.addEventListener('input',render);
try{var q=new URLSearchParams(window.location.search).get('q');if(q){box.value=q;}}catch(e){}
render();}
var pages=document.querySelectorAll('.page');
if(pages.length){root.classList.add('js-router');
function show(){var id=(location.hash||'#pg-index-html').slice(1);var found=false;
pages.forEach(function(p){var on=p.id===id;p.classList.toggle('active',on);if(on){found=true;}});
if(!found){pages[0].classList.add('active');}window.scrollTo(0,0);}
window.addEventListener('hashchange',show);show();}
})();
"""
