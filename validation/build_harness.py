#!/usr/bin/env python3
"""Build audit_v2.html without replacing the legacy harness or migrating answers.

python -B validation/build_harness.py [sample.csv] [audit_v2.html]
Existing generated v2 HTML is archived by content hash before replacement.
Browser storage is never removed. Export from the old harness before switching.
"""
import argparse
import csv
import importlib.util
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_audit", ROOT / "analysis" / "local_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def prepare(sample, input_path):
    with Path(sample).open(encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.DictReader(fh))
    if not raw or len({r["case_id"] for r in raw}) != len(raw):
        raise ValueError("Missing/duplicate sample identities")
    if any(r.get(k) for r in raw for k in ("decision", "capture_year", "notes")):
        raise ValueError("Sample contains historical annotations: preserve/export and reconcile manually first")
    sample_sha = audit.digest_file(sample)
    input_sha = audit.digest_file(input_path)
    seed_path = Path(sample).with_name("audit_sample_seed.txt")
    seed = {}
    if seed_path.is_file():
        seed = dict(line.split("=", 1) for line in seed_path.read_text(encoding="utf-8-sig").splitlines()
                    if "=" in line)
    cases = []
    for r in raw:
        c = {k: r.get(k, "") for k in audit.IDENTITY}
        if r.get("input_sha256") and r["input_sha256"] != input_sha:
            raise ValueError("Sampler input hash differs from the immutable input supplied to builder")
        if not c["feature_id"]:
            way = re.search(r"\(way (\d+)\)", c["osm_assertion"])
            c["feature_id"] = ("way:" + way[1]) if way else (
                c["arm"] + "-coordinate:" + c["lat"] + "," + c["lon"])
        c["radius_m"] = c["radius_m"] or "25"
        c["sampling_frame"] = c["sampling_frame"] or "legacy-grid-retained"
        c["seed"] = c["seed"] or seed.get("seed", "")
        c["osm_link"] = r.get("osm_link", "")
        c["streetview_link"] = r.get("streetview_link", "")
        # Historical profile_state is deliberately not promoted to a graph/local truth field.
        c["mapped_feature_state"] = r.get("mapped_feature_state", "")
        cases.append(c)
    fp = audit.fingerprint(cases, input_sha, sample_sha)
    manifest = {
        "schema_version": audit.SCHEMA, "sample_fingerprint": fp,
        "input_sha256": input_sha, "sample_sha256": sample_sha,
        "input_file": Path(input_path).name, "sample_file": Path(sample).name,
        "cases": len(cases), "cities": len({c["stratum"] for c in cases}),
        "repeat_ids": audit.repeat_ids(cases, fp),
        "schema_fields": audit.FIELDS, "identity_fields": audit.IDENTITY,
        "outcomes": audit.OUTCOMES,
        "sampling_frames": sorted({c["sampling_frame"] for c in cases}),
        "note": "Sidewalk-arm local point audit, 500 cases. "
                "This is a local point audit, not route, acoustic-function or campaign validation.",
    }
    return cases, manifest


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Local feature audit v2</title>
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
.fp{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#8595a6;word-break:break-all}
</style>
<h1>Local feature audit — v2</h1>
<p>No acoustic-function, whole-way, route or campaign-yield judgments.</p>
<details><summary>Instructions and preservation safeguards</summary>
<ol><li>Export all old-browser answers using the OLD harness before switching. This version never reads,
overwrites or migrates its storage. Legacy CSV import is rejected, not guessed.</li>
<li>Use permitted manual imagery inspection or field observation. Check current provider/institutional
permissions; no blanket permission is asserted. Never derive OSM edits from Google imagery.</li>
<li>Judge the marker location only. Record each independent outcome; a sidewalk may be present yet blocked.
Do not infer the whole street or functioning sound from a still image.</li>
<li>Record source, reproducible viewer/reference, capture date (YYYY-MM-DD, or YYYY if only year known),
and today's audit date. Use UNVERIFIABLE with a reason for ambiguity, occlusion, wrong location or no imagery.
Old images can describe the historic image, not establish current presence.</li>
<li>Sidewalk arm: report side(s) relative to the OSM way direction; if presence is certain but direction unclear,
use PRESENT SIDE UNSPECIFIED rather than guessing left/right. Crossing arms: existence, markings, tactile and kerb
are independent. If no crossing exists, mark its attributes NOT APPLICABLE.</li>
<li>Save the case, then navigate. Export often. Repeat round is available only after all primary cases
are saved and at least fourteen days after primary completion; it hides primary answers and OSM assertions.
The independently keyed repeat retains the primary round unchanged.</li></ol></details>
<div class="row"><select id="round"><option value="primary">Primary 500</option>
<option value="repeat">Blinded repeat 10%</option></select>
<button id="prev">Previous</button><button id="next">Next</button>
<input id="jump" type="number" min="1" aria-label="Case position" style="width:90px"><button id="go">Go</button>
<button id="export">Export both rounds</button><button id="import">Import v2 CSV</button>
<input id="file" type="file" accept=".csv" hidden></div>
<p id="status"></p><div id="case"></div><p class="warn" id="error"></p>
<script>
const DATA=__DATA__, META=__META__;
const text=v=>String(v??'');
const esc=v=>text(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function parseCSV(s){
  let rows=[],row=[],v='',q=false;
  s=s.replace(/^\uFEFF/,'');
  for(let i=0;i<s.length;i++){let c=s[i];
    if(q){if(c==='"'){if(s[i+1]==='"'){v+='"';i++;}else q=false;}else v+=c;}
    else if(c==='"')q=true;else if(c===','){row.push(v);v='';}
    else if(c==='\n'){row.push(v);rows.push(row);row=[];v='';}
    else if(c!=='\r')v+=c;
  }
  if(q)throw Error('Unterminated CSV quote');
  if(v||row.length){row.push(v);rows.push(row);}
  const h=rows.shift()||[];return rows.filter(r=>r.some(Boolean)).map(r=>{
    if(r.length!==h.length)throw Error('CSV column count mismatch');
    return Object.fromEntries(h.map((k,i)=>[k,r[i]]));});
}
function validateImport(rows){
  if(!rows.length)throw Error('Empty CSV');
  let seen=new Set();
  for(const r of rows){
    if(META.schema_fields.some(k=>!(k in r)))throw Error('Incomplete v2 schema; no migration performed');
    if(r.schema_version!==META.schema_version || r.sample_fingerprint!==META.sample_fingerprint ||
       r.input_sha256!==META.input_sha256)throw Error('Schema/sample/input mismatch. No answers imported.');
    if(!['primary','repeat'].includes(r.round))throw Error('Unknown round');
    const c=DATA.find(c=>c.case_id===r.case_id);
    if(!c || META.identity_fields.some(k=>text(c[k])!==text(r[k])))throw Error('Case identity mismatch: '+r.case_id);
    if(r.round==='repeat'&&!META.repeat_ids.includes(r.case_id))throw Error('Not a selected repeat case');
    const key=r.round+'|'+r.case_id;if(seen.has(key))throw Error('Duplicate round/case');seen.add(key);
    for(const [k,allowed] of Object.entries(META.outcomes))
      if(r[k]&&!allowed.includes(r[k]))throw Error('Invalid '+k);
  }
  const repeated=rows.filter(r=>r.round==='repeat'&&r.completed==='true');
  if(repeated.length){
    const primary=Object.fromEntries(rows.filter(r=>r.round==='primary').map(r=>[r.case_id,r]));
    if(!repeatReady(primary))throw Error('Repeat CSV requires complete primary data and fourteen-day separation');
    const end=Math.max(...DATA.map(c=>Date.parse(primary[c.case_id].updated_at)));
    if(repeated.some(r=>!Number.isFinite(Date.parse(r.first_audited_at)) ||
      Date.parse(r.first_audited_at)<end+14*86400000))throw Error('Repeat timestamp precedes fourteen-day separation');
  }
  return rows;
}
function repeatReady(primary, now=new Date()){
  if(DATA.some(c=>primary[c.case_id]?.completed!=='true'))return false;
  const dates=DATA.map(c=>Date.parse(primary[c.case_id].updated_at));
  return dates.every(Number.isFinite)&&now.getTime()-Math.max(...dates)>=14*86400000;
}
globalThis.auditTest={parseCSV,validateImport,repeatReady};
if(typeof document!=='undefined'){
  const $=id=>document.getElementById(id);
  const key=round=>'osm-local-audit-v2-'+META.sample_fingerprint+'-'+round;
  let answers={primary:{},repeat:{}},storageOK=true,storageReadFailed=false,index=0;
  for(const round of ['primary','repeat']){
    try{const saved=JSON.parse(localStorage.getItem(key(round))||'null');
      if(saved){if(saved.fingerprint!==META.sample_fingerprint)throw Error('Storage fingerprint mismatch');
        answers[round]=saved.answers||{};}}
    catch(e){storageOK=false;storageReadFailed=true;$('error').textContent='Cannot safely read storage; existing data will not be overwritten: '+e.message;}
  }
  function persist(round){
    if(storageReadFailed){$('error').textContent='Storage read failed: existing bytes preserved. Export this session before closing.';return;}
    try{localStorage.setItem(key(round),JSON.stringify({fingerprint:META.sample_fingerprint,answers:answers[round]}));}
    catch(e){storageOK=false;$('error').textContent='NOT SAVED TO BROWSER. Export before closing. '+e.message;}
  }
  const active=()=> $('round').value;
  const cases=()=>active()==='primary'?DATA:META.repeat_ids.map(id=>DATA.find(c=>c.case_id===id));
  let lastYear='';
  function render(){
    const round=active();if(round==='repeat'&&!repeatReady(answers.primary)){
      $('round').value='primary';$('error').textContent='Repeat locked: finish primary and wait fourteen days.';return render();}
    const list=cases();index=Math.max(0,Math.min(index,list.length-1));const c=list[index],r=answers[round][c.case_id]||{};
    $('jump').value=index+1;$('jump').max=list.length;
    const fields=c.arm==='sidewalk'?['sidewalk']:['crossing_exists','markings','tactile','kerb'];
    $('status').textContent=`${round}: ${index+1}/${list.length}; saved ${list.filter(x=>answers[round][x.case_id]?.completed==='true').length}. `+
      `Storage ${storageOK?'available':'UNAVAILABLE'}. Fingerprint ${META.sample_fingerprint}\nFrame: ${c.sampling_frame}`;
    const LBL={both:'Both sides',left:'Left side',right:'Right side',
      present_side_unspecified:'Present, side unclear',none:'No sidewalk',unverifiable:"Can't tell"};
    const CLS={none:'no',unverifiable:'unk'};
    const done=list.filter(x=>answers[round][x.case_id]?.completed==='true').length;
    const opts=fields.map(k=>META.outcomes[k].map((v,i)=>
      `<button class="opt ${CLS[v]||''} ${r[k]===v?'on':''}" data-pick="${k}" data-val="${v}">
        <span class="k">${i+1}</span><span>${LBL[v]||v}</span></button>`).join('')).join('');
    $('case').innerHTML=`<div class="card">
      <div class="prog"><i style="width:${(done/list.length*100).toFixed(1)}%"></i></div>
      <h2>Does a sidewalk exist at this marker?</h2>
      <div class="meta">${esc(c.case_id)} &middot; ${esc(c.stratum)} &middot; case ${index+1} of ${list.length}
        &middot; ${done} saved${round==='repeat'?' &middot; independent repeat':''}</div>
      <p class="links"><a href="${esc(c.streetview_link)}" target="_blank" rel="noopener">Open Street View &rarr;</a>
      ${round==='primary'?`<a href="${esc(c.osm_link)}" target="_blank" rel="noopener">OSM way</a>`:''}</p>
      <div class="opts">${opts}</div>
      ${round==='primary'?`<details><summary>Mapped assertion (not ground truth)</summary>${esc(c.osm_assertion)}
      <p>Historical mapped-feature tag state only: ${esc(c.mapped_feature_state)||'not supplied'}; not graph state.</p></details>`:''}
      <div class="foot">
        <label>Imagery year <input data-field="capture_date" inputmode="numeric" pattern="\\d{4}"
          maxlength="4" size="4" placeholder="YYYY" value="${esc(r.capture_date||lastYear)}"></label>
        <label id="reasonWrap" style="display:none">Why not<select data-field="unverifiable_reason">
          <option value="">Choose</option>${['no imagery','view obstructed','imagery too old','wrong location','ambiguous']
          .map(v=>`<option ${r.unverifiable_reason===v?'selected':''}>${v}</option>`).join('')}</select></label>
        <button id="save">Save &amp; next <span class="k">&crarr;</span></button>
      </div>
      <input type="hidden" data-field="source" value="Street View">
      <input type="hidden" data-field="source_reference" value="${esc(c.streetview_link)}">
      <input type="hidden" data-field="audit_date" value="${new Date().toISOString().slice(0,10)}">
      <input type="hidden" data-field="notes" value="">
    </div>`;
    const syncReason=()=>{const a=answers[round][c.case_id]||{};
      $('reasonWrap').style.display=fields.some(k=>a[k]==='unverifiable')?'flex':'none';};
    document.querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{
      const a=answers[round][c.case_id]||{};a[b.dataset.pick]=b.dataset.val;a.completed='false';
      a.updated_at=new Date().toISOString();answers[round][c.case_id]=a;persist(round);
      document.querySelectorAll(`[data-pick="${b.dataset.pick}"]`).forEach(x=>x.classList.remove('on'));
      b.classList.add('on');syncReason();});
    syncReason();
    document.querySelectorAll('[data-field]').forEach(el=>el.onchange=()=>{
      const a=answers[round][c.case_id]||{};a[el.dataset.field]=el.value;a.completed='false';
      a.updated_at=new Date().toISOString();answers[round][c.case_id]=a;persist(round);});
    $('save').onclick=()=>{
      const a=answers[round][c.case_id]||{};
      document.querySelectorAll('[data-field]').forEach(el=>a[el.dataset.field]=el.value);
      try{
        if(!a.source||!a.audit_date)throw Error('Source and audit date required');
        if(fields.some(k=>!a[k]))throw Error('Answer each independent outcome');
        if(fields.some(k=>a[k]==='unverifiable')&&!a.unverifiable_reason.trim())throw Error('Unverifiable reason required');
        const verifiable=fields.some(k=>!['unverifiable','not_applicable'].includes(a[k]));
        if(verifiable && (!a.source_reference||a.source==='none'||!/^\d{4}(-\d{2}-\d{2})?$/.test(a.capture_date)))
          throw Error('Verifiable observations require source/reference and capture date/year');
        if(a.capture_date && (a.capture_date.length===10 &&
          (!Number.isFinite(Date.parse(a.capture_date)) || new Date(a.capture_date).toISOString().slice(0,10)!==a.capture_date)))
          throw Error('Invalid capture date');
        if(a.audit_date>new Date().toISOString().slice(0,10) ||
          (a.capture_date && (a.capture_date.length===4 ? a.capture_date>a.audit_date.slice(0,4) : a.capture_date>a.audit_date)))
          throw Error('Capture/audit dates cannot be in the future');
        if(c.arm!=='sidewalk' && a.crossing_exists==='absent' && ['markings','tactile','kerb'].some(k=>a[k]!=='not_applicable'))
          throw Error('Absent crossing: use NOT APPLICABLE for crossing attributes');
        if(c.arm!=='sidewalk' && a.crossing_exists==='present' && ['markings','tactile','kerb'].some(k=>a[k]==='not_applicable'))
          throw Error('Present crossing: record each attribute or UNVERIFIABLE');
        lastYear=a.capture_date||lastYear;
        a.completed='true';a.first_audited_at=a.first_audited_at||new Date().toISOString();a.updated_at=new Date().toISOString();
        answers[round][c.case_id]=a;persist(round);if(storageOK)$('error').textContent='';index=Math.min(index+1,list.length-1);render();
      }catch(e){$('error').textContent=e.message;}
    };
  }
  document.addEventListener('keydown',e=>{
    if(e.target.matches('input,select,textarea'))return;
    const btns=[...document.querySelectorAll('[data-pick]')];
    if(/^[1-9]$/.test(e.key)&&btns[+e.key-1]){btns[+e.key-1].click();e.preventDefault();}
    else if(e.key==='Enter'&&$('save')){$('save').click();e.preventDefault();}
    else if(e.key==='ArrowLeft'){index--;render();}
    else if(e.key==='ArrowRight'){index++;render();}});
  $('prev').onclick=()=>{index--;render();};$('next').onclick=()=>{index++;render();};
  function resume(){index=Math.max(0,cases().findIndex(c=>answers[active()][c.case_id]?.completed!=='true'));render();}
  $('go').onclick=()=>{index=(Number($('jump').value)||1)-1;render();};
  $('round').onchange=resume;
  $('export').onclick=()=>{
    const q=v=>/[",\n\r]/.test(text(v))?'"'+text(v).replace(/"/g,'""')+'"':text(v);
    let out=[META.schema_fields.join(',')];
    for(const round of ['primary','repeat'])for(const c of DATA){
      if(round==='repeat'&&!META.repeat_ids.includes(c.case_id))continue;
      const r={...c,...answers[round][c.case_id],schema_version:META.schema_version,
        sample_fingerprint:META.sample_fingerprint,input_sha256:META.input_sha256,round};
      out.push(META.schema_fields.map(k=>q(r[k])).join(','));
    }
    const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\uFEFF'+out.join('\n')],{type:'text/csv'}));
    a.download='audit_results_v2.csv';document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  };
  $('import').onclick=()=>$('file').click();
  $('file').onchange=async()=>{
    try{
      const rows=validateImport(parseCSV(await $('file').files[0].text()));
      const next=JSON.parse(JSON.stringify(answers));
      for(const r of rows){
        const old=next[r.round][r.case_id];
        const answerFields=['source','source_reference','capture_date','audit_date','unverifiable_reason',
          ...Object.keys(META.outcomes),'notes','completed','first_audited_at','updated_at'];
        if(old && old.completed==='true' && answerFields.some(k=>text(old[k])!==text(r[k])))
          throw Error('Existing completed answer conflict: '+r.case_id+'. Export current answers; use isolated browser for reconciliation.');
        if(r.updated_at||r.completed==='true')next[r.round][r.case_id]=r;
      }
      answers=next;persist('primary');persist('repeat');
      if(storageOK)$('error').textContent='Imported exact v2 sample.';resume();
    }catch(e){$('error').textContent='IMPORT REJECTED: '+e.message;}
    $('file').value='';
  };
  resume();
}
</script></html>"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sample", nargs="?", default=str(ROOT / "results" / "census" / "audit_sample.csv"))
    p.add_argument("output", nargs="?", default=str(ROOT / "validation" / "audit_v2.html"))
    p.add_argument("--input", default=str(ROOT / "data" / "greece-latest.osm.pbf"))
    args = p.parse_args()
    cases, manifest = prepare(args.sample, args.input)
    counts = {}
    for c in cases:
        k = (c["stratum"], c["arm"])
        counts[k] = counts.get(k, 0) + 1
    # Approved allocation, amendment 2026-09-15b: the sidewalk arm only, ten per city.
    # The crossing arms were dropped because their affirmative subsamples were too thin
    # to support the accuracy claims they were drawn for. Still a hard guard: the
    # builder never silently resizes or redraws a sample.
    arms = {a for _, a in counts}
    if (len(cases) != 500 or arms != {"sidewalk"} or len(counts) != 50
            or set(counts.values()) != {10} or manifest["cities"] != 50):
        raise ValueError("Approved allocation is exactly 50 cities x sidewalk x 10; "
                         "no automatic redraw/resizing")
    out = Path(args.output)
    if out.name == "audit.html":
        raise ValueError("Legacy audit.html is protected; use audit_v2.html")
    # Escape script delimiters in user/source strings without changing their JS values.
    encode = lambda x: json.dumps(x, ensure_ascii=False).replace("<", "\\u003c")
    html = PAGE.replace("__DATA__", encode(cases)).replace("__META__", encode(manifest))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.read_text(encoding="utf-8") != html:
        archive = out.parent / "archive" / (out.stem + "." + audit.digest_file(out) + ".html")
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            shutil.copy2(out, archive)
        print("Preserved previous generated harness:", archive)
    out.write_text(html, encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("schema_version", "sample_fingerprint",
                     "input_sha256", "sample_sha256", "cases", "cities", "sampling_frames")},
                     ensure_ascii=False, indent=2))
    print("Blinded repeated cases:", len(manifest["repeat_ids"]))
    print("Wrote", out, "; legacy HTML/sample/browser keys untouched. Export old-browser answers first.")


if __name__ == "__main__":
    main()
