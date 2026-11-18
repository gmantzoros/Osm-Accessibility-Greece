#!/usr/bin/env python3
"""Build the whole-way continuity page from results/census/continuity_sample.csv.

Separate from the local-feature harness on purpose: different sample, different
estimand, different storage key. Nothing here reads or writes the sidewalk
audit's answers.

The three points of a way are presented consecutively so the observer sees the
way as a unit, but each point is recorded independently; the analysis compares
them rather than asking for a single whole-way judgement.

    python validation/build_continuity_harness.py
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "results" / "census" / "continuity_sample.csv"
OUT = ROOT / "validation" / "continuity.html"
SCHEMA = "way-continuity-v1"
OUTCOMES = ["both", "left", "right", "present_side_unspecified", "none", "unverifiable"]
REASONS = ["no imagery", "view obstructed", "imagery too old", "wrong location", "ambiguous"]

PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Whole-way continuity audit</title>
<style>
*{box-sizing:border-box}
body{font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;max-width:860px;margin:0 auto;
padding:18px;background:#eef1f5;color:#1b2836}
h1{font-size:19px;margin:0 0 2px}h2{font-size:16px;margin:0 0 2px}
button,input,select{font:inherit}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
.bar button,.bar select,.bar input{padding:6px 10px;border:1px solid #c3ccd8;border-radius:7px;background:#fff}
.bar button:hover{border-color:#4d6a8a;cursor:pointer}
.card{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:16px;margin:10px 0;
box-shadow:0 1px 2px rgba(20,40,60,.05)}
.meta{color:#5d6d7e;font-size:13px;margin:2px 0 10px}
.links a{display:inline-block;margin:0 14px 0 0;font-weight:600;color:#1a5aa0}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:4px 0 2px}
.opt{display:flex;align-items:center;gap:9px;padding:13px 12px;border:2px solid #cfd8e3;border-radius:10px;
background:#fff;text-align:left;cursor:pointer}
.opt:hover{border-color:#4d6a8a;background:#f7fafd}
.opt.on{border-color:#1a5aa0;background:#e8f1fb;box-shadow:inset 0 0 0 1px #1a5aa0}
.opt.no.on{border-color:#a8431c;background:#fdeee7;box-shadow:inset 0 0 0 1px #a8431c}
.opt.unk.on{border-color:#6b6b6b;background:#f0f0f0;box-shadow:inset 0 0 0 1px #6b6b6b}
.k{display:inline-grid;place-items:center;min-width:23px;height:23px;border-radius:6px;background:#eef2f7;
border:1px solid #c3ccd8;font-size:12px;font-weight:700;color:#41566d}
.dots{display:flex;gap:6px;align-items:center;margin:2px 0 8px}
.dot{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;
border:2px solid #cfd8e3;background:#fff;color:#41566d}
.dot.cur{border-color:#1a5aa0;color:#1a5aa0}.dot.done{background:#1a5aa0;border-color:#1a5aa0;color:#fff}
.foot{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:12px}
.foot label{font-size:13px;color:#41566d;display:flex;align-items:center;gap:6px}
.foot input,.foot select{padding:7px 9px;border:1px solid #c3ccd8;border-radius:7px}
#save{padding:11px 18px;border:0;border-radius:9px;background:#1a5aa0;color:#fff;font-weight:700;cursor:pointer}
#save:hover{background:#15497f}
.prog{height:7px;background:#dde3ea;border-radius:99px;overflow:hidden;margin:6px 0 2px}
.prog>i{display:block;height:100%;background:#1a5aa0}
.warn{color:#a8431c;font-weight:700;min-height:19px}
#status{color:#5d6d7e;font-size:12.5px;white-space:pre-wrap}
details{background:#fff;border:1px solid #dde3ea;border-radius:10px;padding:11px;margin:8px 0}
summary{cursor:pointer;font-weight:600}
</style>
<h1>Whole-way continuity audit</h1>
<p>Three points on each selected way, judged independently. No whole-way verdict is requested.</p>
<details><summary>What this is for</summary>
<ol><li>The survey-priority scenario assumes a selected way becomes passable along its whole length.
These observations test that assumption; they are not a sidewalk prevalence estimate and are not
pooled with the local-feature audit.</li>
<li>Judge each marker on its own. Do not carry an answer forward from the previous point, and do not
infer the rest of the way.</li>
<li>Many of these ways are rural. If there is no imagery, record UNVERIFIABLE with a reason and move
on; that outcome is itself informative about survey cost.</li>
<li>Geometry was read from the OpenStreetMap API on the date shown, not from the pinned extract.</li>
<li>Manual inspection only. Never derive OpenStreetMap edits from Google imagery.</li></ol></details>
<div class="bar">
<button id="prev">Previous</button><button id="next">Next</button>
<input id="jump" type="number" min="1" aria-label="Case position" style="width:90px"><button id="go">Go</button>
<button id="export">Export CSV</button><button id="import">Import CSV</button>
<input id="file" type="file" accept=".csv" hidden></div>
<p id="status"></p><div id="case"></div><p class="warn" id="error"></p>
<script>
const DATA=__DATA__, META=__META__;
const KEY='way-continuity-'+META.sample_sha256.slice(0,16);
const text=v=>String(v??'');
const esc=v=>text(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=id=>document.getElementById(id);
let answers={},storageOK=true,index=0,lastYear='';
try{answers=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){storageOK=false;}
function persist(){try{localStorage.setItem(KEY,JSON.stringify(answers));}catch(e){storageOK=false;
  $('error').textContent='Storage unavailable: export before closing.';}}
function csvCell(v){v=text(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
function render(){
  index=Math.max(0,Math.min(index,DATA.length-1));
  const c=DATA[index],r=answers[c.case_id]||{};
  const done=DATA.filter(x=>answers[x.case_id]?.completed==='true').length;
  const group=DATA.filter(x=>x.way_group===c.way_group);
  const LBL={both:'Both sides',left:'Left side',right:'Right side',
    present_side_unspecified:'Present, side unclear',none:'No sidewalk',unverifiable:"Can't tell"};
  const CLS={none:'no',unverifiable:'unk'};
  $('jump').value=index+1;$('jump').max=DATA.length;
  $('status').textContent=`${index+1}/${DATA.length}; saved ${done}. Storage `+
    `${storageOK?'available':'UNAVAILABLE'}. Sample ${META.sample_sha256.slice(0,16)}`;
  const dots=group.map(g=>{const st=answers[g.case_id]?.completed==='true'?'done':
    (g.case_id===c.case_id?'cur':'');return `<span class="dot ${st}">${g.point_index}</span>`;}).join('');
  $('case').innerHTML=`<div class="card">
    <div class="prog"><i style="width:${(done/DATA.length*100).toFixed(1)}%"></i></div>
    <h2>Is a sidewalk present at this point?</h2>
    <div class="meta">Way ${esc(c.way_group)} of ${META.ways} &middot; ${esc(c.highway)} &middot;
      ${esc(c.own_km)} km &middot; ${esc(c.band)} band &middot; worklist rank ${esc(c.worklist_rank)}</div>
    <div class="dots">${dots}<span class="meta" style="margin:0 0 0 8px">point ${esc(c.point_index)} of 3,
      at ${Math.round(Number(c.point_fraction)*100)}% along the way</span></div>
    <p class="links"><a href="${esc(c.streetview_link)}" target="_blank" rel="noopener">Open Street View &rarr;</a>
      <a href="${esc(c.osm_link)}" target="_blank" rel="noopener">OSM way</a></p>
    <div class="opts">${META.outcomes.map((v,i)=>
      `<button class="opt ${CLS[v]||''} ${r.sidewalk===v?'on':''}" data-val="${v}">
        <span class="k">${i+1}</span><span>${LBL[v]||v}</span></button>`).join('')}</div>
    <div class="foot">
      <label>Imagery year <input id="yr" inputmode="numeric" maxlength="4" size="4" placeholder="YYYY"
        value="${esc(r.capture_year||lastYear)}"></label>
      <label id="reasonWrap" style="display:none">Why not <select id="reason">
        <option value="">Choose</option>${META.reasons.map(v=>
        `<option ${r.unverifiable_reason===v?'selected':''}>${v}</option>`).join('')}</select></label>
      <button id="save">Save &amp; next <span class="k">&crarr;</span></button>
    </div></div>`;
  const syncReason=()=>{$('reasonWrap').style.display=
    (answers[c.case_id]||{}).sidewalk==='unverifiable'?'flex':'none';};
  document.querySelectorAll('[data-val]').forEach(b=>b.onclick=()=>{
    const a=answers[c.case_id]||{};a.sidewalk=b.dataset.val;a.completed='false';
    answers[c.case_id]=a;persist();
    document.querySelectorAll('[data-val]').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');syncReason();});
  syncReason();
  $('save').onclick=()=>{
    const a=answers[c.case_id]||{};
    a.capture_year=$('yr').value.trim();a.unverifiable_reason=$('reason')?$('reason').value:'';
    try{
      if(!a.sidewalk)throw Error('Choose an outcome');
      if(a.sidewalk==='unverifiable'&&!a.unverifiable_reason)throw Error('Unverifiable needs a reason');
      if(a.sidewalk!=='unverifiable'&&!/^\d{4}$/.test(a.capture_year))throw Error('Imagery year required (YYYY)');
      const y=new Date().getFullYear();
      if(a.capture_year&&(Number(a.capture_year)>y||Number(a.capture_year)<2007))throw Error('Implausible year');
      lastYear=a.capture_year||lastYear;
      a.completed='true';a.audit_date=new Date().toISOString().slice(0,10);
      a.updated_at=new Date().toISOString();answers[c.case_id]=a;persist();
      $('error').textContent='';index=Math.min(index+1,DATA.length-1);render();
    }catch(e){$('error').textContent=e.message;}};
}
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,select'))return;
  const btns=[...document.querySelectorAll('[data-val]')];
  if(/^[1-6]$/.test(e.key)&&btns[+e.key-1]){btns[+e.key-1].click();e.preventDefault();}
  else if(e.key==='Enter'&&$('save')){$('save').click();e.preventDefault();}
  else if(e.key==='ArrowLeft'){index--;render();}
  else if(e.key==='ArrowRight'){index++;render();}});
$('prev').onclick=()=>{index--;render();};
$('next').onclick=()=>{index++;render();};
$('go').onclick=()=>{index=(Number($('jump').value)||1)-1;render();};
$('export').onclick=()=>{
  const cols=['schema_version','sample_sha256',...META.identity,'sidewalk','capture_year',
    'unverifiable_reason','audit_date','completed','updated_at'];
  const lines=[cols.join(',')];
  for(const c of DATA){const a=answers[c.case_id]||{};
    lines.push(cols.map(k=>csvCell(k==='schema_version'?META.schema:
      k==='sample_sha256'?META.sample_sha256:(c[k]??a[k]??''))).join(','));}
  const blob=new Blob([lines.join('\n')],{type:'text/csv'});
  const u=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=u;a.download='continuity_results.csv';a.click();URL.revokeObjectURL(u);};
$('import').onclick=()=>$('file').click();
$('file').onchange=ev=>{const f=ev.target.files[0];if(!f)return;const rd=new FileReader();
  rd.onload=()=>{try{
    const rows=rd.result.split(/\r?\n/).filter(Boolean).map(l=>l.split(','));
    const h=rows.shift();const iId=h.indexOf('case_id'),iS=h.indexOf('sidewalk');
    const iSha=h.indexOf('sample_sha256');
    if(iId<0||iS<0)throw Error('Not a continuity export');
    if(iSha>=0&&rows.length&&rows[0][iSha].replace(/"/g,'')!==META.sample_sha256)
      throw Error('Different sample; no answers imported');
    let n=0;for(const r of rows){const id=r[iId];if(!DATA.some(c=>c.case_id===id))continue;
      answers[id]={sidewalk:r[iS],capture_year:r[h.indexOf('capture_year')]||'',
        unverifiable_reason:r[h.indexOf('unverifiable_reason')]||'',
        audit_date:r[h.indexOf('audit_date')]||'',completed:r[h.indexOf('completed')]||'true'};n++;}
    persist();$('error').textContent='';alert('Imported '+n+' rows');render();
  }catch(e){$('error').textContent=e.message;}};
  rd.readAsText(f);};
index=Math.max(0,DATA.findIndex(c=>answers[c.case_id]?.completed!=='true'));
render();
</script>
"""


def main():
    rows = list(csv.DictReader(io.open(SAMPLE, encoding="utf-8")))
    if not rows:
        raise SystemExit("Empty continuity sample")
    ways = len({r["way_group"] for r in rows})
    per_way = {}
    for r in rows:
        per_way[r["way_group"]] = per_way.get(r["way_group"], 0) + 1
    if set(per_way.values()) != {3}:
        raise ValueError("Every way must carry exactly three observation points")
    sha = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    identity = ["case_id", "way_group", "osm_way_id", "point_index", "point_fraction", "band",
                "pool_n", "draw_n", "inclusion_probability", "worklist_rank", "highway",
                "stratum", "own_km", "lat", "lon", "geometry_source", "geometry_fetched_on",
                "current_sidewalk_tag"]
    meta = {"schema": SCHEMA, "sample_sha256": sha, "ways": ways, "cases": len(rows),
            "outcomes": OUTCOMES, "reasons": REASONS, "identity": identity}
    if OUT.exists():                       # never silently replace annotated work
        prev = hashlib.sha256(OUT.read_bytes()).hexdigest()
        archive = OUT.parent / "archive" / f"continuity.{prev}.html"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(OUT.read_bytes())
        print(f"Preserved previous page: {archive}")
    page = (PAGE.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
                .replace("__META__", json.dumps(meta, ensure_ascii=False)))
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  {ways} ways, {len(rows)} observations, sample sha256 {sha[:16]}")


if __name__ == "__main__":
    main()
