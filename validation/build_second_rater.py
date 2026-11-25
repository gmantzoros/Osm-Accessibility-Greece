#!/usr/bin/env python3
"""Build a blinded second-rater form for 100 of the 500 sidewalk-audit points.

python -B validation/build_second_rater.py [sample.csv] [second_rater_audit.html]

Selection is outcome-independent: two points per community (50 x 2 = 100),
ordered by SHA-256 of the primary sample fingerprint, a fixed label and the
case id, so the draw is reproducible and does not look at any judgment. The
form shows no first-round answers, no OSM tag assertion and no first-round
imagery date. The second rater judges the same marker with the same options,
records the imagery year, and exports a CSV for analysis/inter_rater.py.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "second-rater-2026-11"
PER_COMMUNITY = 2
OPTIONS = ["both", "left", "right", "present_side_unspecified", "none", "unverifiable"]


def select(sample_csv, fingerprint):
    with open(sample_csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_comm = {}
    for r in rows:
        by_comm.setdefault(r["stratum"], []).append(r)
    chosen = []
    for community in sorted(by_comm):
        ranked = sorted(by_comm[community], key=lambda r: hashlib.sha256(
            (fingerprint + "|" + LABEL + "|" + r["case_id"]).encode()).digest())
        chosen.extend(ranked[:PER_COMMUNITY])
    order = sorted(chosen, key=lambda r: hashlib.sha256(
        (fingerprint + "|" + LABEL + "|order|" + r["case_id"]).encode()).digest())
    return [{"case_id": r["case_id"], "stratum": r["stratum"], "lat": r["lat"], "lon": r["lon"],
             "feature_id": r["feature_id"], "osm_link": r["osm_link"],
             "streetview_link": r["streetview_link"]} for r in order]


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sidewalk audit: second rater</title>
<style>
*{box-sizing:border-box}
body{font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;max-width:860px;margin:0 auto;
padding:18px;background:#eef1f5;color:#1b2836}
h1{font-size:19px;margin:0 0 2px}h2{font-size:16px;margin:0 0 2px}
button,input,select{font:inherit}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
.row button,.row input{padding:6px 10px;border:1px solid #c3ccd8;border-radius:7px;background:#fff}
.row button:hover{border-color:#4d6a8a;cursor:pointer}
.card{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:16px;margin:10px 0;
box-shadow:0 1px 2px rgba(20,40,60,.05)}
.meta{color:#5d6d7e;font-size:13px;margin:2px 0 10px}
.links a{display:inline-block;margin:0 14px 6px 0;font-weight:600;color:#1a5aa0}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:4px 0 2px}
.opt{display:flex;align-items:center;gap:9px;padding:13px 12px;border:2px solid #cfd8e3;border-radius:10px;
background:#fff;text-align:left;cursor:pointer}
.opt:hover{border-color:#4d6a8a;background:#f7fafd}
.opt.on{border-color:#1a5aa0;background:#e8f1fb;box-shadow:inset 0 0 0 1px #1a5aa0}
.opt.no.on{border-color:#a8431c;background:#fdeee7;box-shadow:inset 0 0 0 1px #a8431c}
.opt.unk.on{border-color:#6b6b6b;background:#f0f0f0;box-shadow:inset 0 0 0 1px #6b6b6b}
.k{display:inline-grid;place-items:center;min-width:23px;height:23px;border-radius:6px;background:#eef2f7;
border:1px solid #c3ccd8;font-size:12px;font-weight:700;color:#41566d}
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
#rater{width:150px}
</style>
<h1>Sidewalk audit: independent second rater (100 points)</h1>
<p>You judge the same markers as the first audit, without seeing its answers. Work on your own;
do not discuss cases with the first auditor until you have exported your answers.</p>
<details open><summary>Instructions (read once)</summary>
<ol>
<li>Enter your initials below. They are saved in the export.</li>
<li>For each point, open Street View. The marker is the coordinate in the link. Judge
<b>only the spot at the marker</b>, not the rest of the street.</li>
<li>Choose what you see at that spot: a sidewalk on <b>both sides</b>, on <b>one side</b> (left or right of the
OSM way direction; open the OSM link to see the way, or choose <i>Present, side unclear</i>),
<b>no sidewalk</b>, or <b>can't tell</b>. A sidewalk is a raised or clearly separated walking strip beside
the carriageway; a painted shoulder or parking strip is not a sidewalk.</li>
<li>Type the year of the imagery you used (shown in Street View, e.g. "Image capture: May 2024").
Use the most recent imagery available at the spot. If you choose <i>Can't tell</i>, pick a reason.</li>
<li>Press <b>Save &amp; next</b> (or Enter). Keys 1-6 pick an option. Answers stay in this browser;
press <b>Export CSV</b> when you finish (and now and then as a backup) and send the file back.</li>
</ol></details>
<div class="row"><label>Your initials <input id="rater" maxlength="12" autocomplete="off"></label>
<button id="prev">Previous</button><button id="next">Next</button>
<input id="jump" type="number" min="1" aria-label="Case position" style="width:80px"><button id="go">Go</button>
<button id="export">Export CSV</button><button id="import">Import my CSV</button>
<input id="file" type="file" accept=".csv" hidden></div>
<p id="status"></p><div id="case"></div><p class="warn" id="error"></p>
<script>
const DATA=__DATA__, META=__META__;
const text=v=>String(v??'');
const esc=v=>text(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const FIELDS=['schema','selection_fingerprint','primary_fingerprint','case_id','stratum','lat','lon','feature_id',
  'rater','sidewalk','capture_date','unverifiable_reason','notes','audit_date','first_audited_at','updated_at','completed'];
const LBL={both:'Both sides',left:'Left side',right:'Right side',
  present_side_unspecified:'Present, side unclear',none:'No sidewalk',unverifiable:"Can't tell"};
const CLS={none:'no',unverifiable:'unk'};
function parseCSV(s){
  let rows=[],row=[],v='',q=false;s=s.replace(/^﻿/,'');
  for(let i=0;i<s.length;i++){let c=s[i];
    if(q){if(c==='"'){if(s[i+1]==='"'){v+='"';i++;}else q=false;}else v+=c;}
    else if(c==='"')q=true;else if(c===','){row.push(v);v='';}
    else if(c==='\n'){row.push(v);rows.push(row);row=[];v='';}
    else if(c!=='\r')v+=c;}
  if(q)throw Error('Unterminated CSV quote');
  if(v||row.length){row.push(v);rows.push(row);}
  const h=rows.shift()||[];return rows.filter(r=>r.some(Boolean)).map(r=>{
    if(r.length!==h.length)throw Error('CSV column count mismatch');
    return Object.fromEntries(h.map((k,i)=>[k,r[i]]));});
}
const $=id=>document.getElementById(id);
const KEY='osm-sidewalk-second-rater-'+META.selection_fingerprint;
let answers={},rater='',storageOK=true,index=0,lastYear='';
try{const saved=JSON.parse(localStorage.getItem(KEY)||'null');
  if(saved){answers=saved.answers||{};rater=saved.rater||'';}}
catch(e){storageOK=false;}
function persist(){
  try{localStorage.setItem(KEY,JSON.stringify({answers,rater}));}
  catch(e){storageOK=false;$('error').textContent='NOT SAVED IN BROWSER - export before closing. '+e.message;}
}
$('rater').value=rater;
$('rater').oninput=()=>{rater=$('rater').value.trim();persist();};
function render(){
  index=Math.max(0,Math.min(index,DATA.length-1));
  const c=DATA[index],r=answers[c.case_id]||{};
  const done=DATA.filter(x=>answers[x.case_id]?.completed==='true').length;
  $('jump').value=index+1;$('jump').max=DATA.length;
  $('status').textContent=`Point ${index+1} of ${DATA.length}; ${done} saved. Browser storage ${storageOK?'available':'UNAVAILABLE - export often'}.`;
  const opts=META.options.map((v,i)=>`<button class="opt ${CLS[v]||''} ${r.sidewalk===v?'on':''}" data-val="${v}">
      <span class="k">${i+1}</span><span>${LBL[v]}</span></button>`).join('');
  $('case').innerHTML=`<div class="card">
    <div class="prog"><i style="width:${(done/DATA.length*100).toFixed(1)}%"></i></div>
    <h2>Is there a sidewalk at this marker?</h2>
    <div class="meta">${esc(c.case_id)} &middot; ${esc(c.stratum.replace('DIMOS:',''))} &middot; ${esc(c.lat)}, ${esc(c.lon)}</div>
    <p class="links"><a href="${esc(c.streetview_link)}" target="_blank" rel="noopener">Open Street View &rarr;</a>
    <a href="${esc(c.osm_link)}" target="_blank" rel="noopener">OSM map (way direction)</a></p>
    <div class="opts">${opts}</div>
    <div class="foot">
      <label>Imagery year <input id="year" inputmode="numeric" maxlength="4" size="4" placeholder="YYYY"
        value="${esc(r.capture_date||lastYear)}"></label>
      <label id="reasonWrap" style="display:${r.sidewalk==='unverifiable'?'flex':'none'}">Why
        <select id="reason"><option value="">Choose</option>${['no imagery','view obstructed','imagery too old',
        'wrong location','ambiguous'].map(v=>`<option ${r.unverifiable_reason===v?'selected':''}>${v}</option>`).join('')}</select></label>
      <label>Note <input id="note" maxlength="200" size="22" value="${esc(r.notes||'')}"></label>
      <button id="save">Save &amp; next <span class="k">&crarr;</span></button>
    </div></div>`;
  document.querySelectorAll('[data-val]').forEach(b=>b.onclick=()=>{
    const a=answers[c.case_id]||{};a.sidewalk=b.dataset.val;a.completed='false';a.updated_at=new Date().toISOString();
    answers[c.case_id]=a;persist();
    document.querySelectorAll('[data-val]').forEach(x=>x.classList.remove('on'));b.classList.add('on');
    $('reasonWrap').style.display=a.sidewalk==='unverifiable'?'flex':'none';});
  $('save').onclick=save;
}
function save(){
  const c=DATA[index],a=answers[c.case_id]||{};
  a.capture_date=$('year').value.trim();a.unverifiable_reason=$('reason').value;a.notes=$('note').value.trim();
  try{
    if(!rater)throw Error('Enter your initials first');
    if(!a.sidewalk)throw Error('Choose an answer');
    if(a.sidewalk==='unverifiable'&&!a.unverifiable_reason)throw Error('Choose why you cannot tell');
    if(a.sidewalk!=='unverifiable'&&!/^\d{4}$/.test(a.capture_date))throw Error('Type the imagery year (YYYY)');
    if(a.capture_date&&(+a.capture_date<2000||+a.capture_date>new Date().getFullYear()))throw Error('Imagery year looks wrong');
    lastYear=a.capture_date||lastYear;
    a.rater=rater;a.audit_date=new Date().toISOString().slice(0,10);
    a.first_audited_at=a.first_audited_at||new Date().toISOString();a.updated_at=new Date().toISOString();
    a.completed='true';answers[c.case_id]=a;persist();
    if(storageOK)$('error').textContent='';
    const next=DATA.findIndex((x,i)=>i>index&&answers[x.case_id]?.completed!=='true');
    index=next>=0?next:Math.min(index+1,DATA.length-1);render();
    if(DATA.every(x=>answers[x.case_id]?.completed==='true'))$('error').textContent='All 100 points saved. Press Export CSV and send the file.';
  }catch(e){$('error').textContent=e.message;}
}
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,select,textarea'))return;
  const btns=[...document.querySelectorAll('[data-val]')];
  if(/^[1-6]$/.test(e.key)&&btns[+e.key-1]){btns[+e.key-1].click();e.preventDefault();}
  else if(e.key==='Enter'&&$('save')){save();e.preventDefault();}
  else if(e.key==='ArrowLeft'){index--;render();}
  else if(e.key==='ArrowRight'){index++;render();}});
$('prev').onclick=()=>{index--;render();};$('next').onclick=()=>{index++;render();};
$('go').onclick=()=>{index=(Number($('jump').value)||1)-1;render();};
$('export').onclick=()=>{
  const q=v=>/[",\n\r]/.test(text(v))?'"'+text(v).replace(/"/g,'""')+'"':text(v);
  const out=[FIELDS.join(',')];
  for(const c of DATA){const r={...c,...answers[c.case_id],schema:META.schema,
    selection_fingerprint:META.selection_fingerprint,primary_fingerprint:META.primary_fingerprint};
    out.push(FIELDS.map(k=>q(r[k])).join(','));}
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob(['﻿'+out.join('\n')],{type:'text/csv'}));
  a.download='second_rater_'+(rater||'unnamed').replace(/[^A-Za-z0-9]/g,'')+'.csv';
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
};
$('import').onclick=()=>$('file').click();
$('file').onchange=async()=>{
  try{
    const rows=parseCSV(await $('file').files[0].text());
    if(!rows.length||rows.some(r=>r.selection_fingerprint!==META.selection_fingerprint))
      throw Error('This CSV is not an export of this form');
    for(const r of rows){if(!DATA.some(c=>c.case_id===r.case_id))throw Error('Unknown case '+r.case_id);
      if(r.sidewalk&&!META.options.includes(r.sidewalk))throw Error('Invalid answer in '+r.case_id);}
    for(const r of rows)if(r.updated_at)answers[r.case_id]={sidewalk:r.sidewalk,capture_date:r.capture_date,
      unverifiable_reason:r.unverifiable_reason,notes:r.notes,rater:r.rater,audit_date:r.audit_date,
      first_audited_at:r.first_audited_at,updated_at:r.updated_at,completed:r.completed};
    rater=rows.find(r=>r.rater)?.rater||rater;$('rater').value=rater;persist();
    $('error').textContent='Imported '+rows.filter(r=>r.completed==='true').length+' saved answers.';render();
  }catch(e){$('error').textContent='IMPORT REJECTED: '+e.message;}
  $('file').value='';
};
index=Math.max(0,DATA.findIndex(c=>answers[c.case_id]?.completed!=='true'));render();
</script></html>"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sample", nargs="?", default=str(ROOT / "results" / "census" / "audit_sample.csv"))
    p.add_argument("output", nargs="?", default=str(ROOT / "validation" / "second_rater_audit.html"))
    p.add_argument("--primary-results", default=str(ROOT / "validation" / "audit_results_v2.csv"))
    args = p.parse_args()
    with open(args.primary_results, encoding="utf-8-sig", newline="") as fh:
        fingerprints = {r["sample_fingerprint"] for r in csv.DictReader(fh)}
    if len(fingerprints) != 1:
        raise ValueError("Primary results must carry exactly one sample fingerprint")
    primary_fp = fingerprints.pop()
    cases = select(args.sample, primary_fp)
    if len(cases) != 100 or len({c["stratum"] for c in cases}) != 50:
        raise ValueError("Expected 2 points in each of 50 communities")
    selection_fp = hashlib.sha256((primary_fp + "|" + LABEL + "|" +
                                   ",".join(sorted(c["case_id"] for c in cases))).encode()).hexdigest()[:16]
    meta = {"schema": "sidewalk-second-rater-v1", "label": LABEL, "primary_fingerprint": primary_fp,
            "selection_fingerprint": selection_fp, "options": OPTIONS, "n": len(cases)}
    encode = lambda x: json.dumps(x, ensure_ascii=False).replace("<", "\\u003c")
    html = PAGE.replace("__DATA__", encode(cases)).replace("__META__", encode(meta))
    out = Path(args.output)
    out.write_text(html, encoding="utf-8")
    (out.with_suffix(".selection.json")).write_text(json.dumps(
        {**meta, "case_ids": sorted(c["case_id"] for c in cases)}, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    print("Wrote", out)


if __name__ == "__main__":
    main()
