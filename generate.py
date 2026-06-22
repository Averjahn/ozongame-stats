#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OzonGame Stats Generator
Queries medtest2 → writes self-contained index.html

Env vars (all optional, have defaults for local dev via SSH tunnel):
  DB_HOST      default: 127.0.0.1
  DB_PORT      default: 5433        (tunnel port)
  DB_NAME      default: medtest2
  DB_USER      default: goadmin
  DB_PASSWORD  required
"""

import os, sys, json, decimal, argparse
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    sys.exit("psycopg2 not found: pip install psycopg2-binary")

DB = dict(
    host     = os.getenv("DB_HOST", "127.0.0.1"),
    port     = int(os.getenv("DB_PORT", "5433")),
    dbname   = os.getenv("DB_NAME", "medtest2"),
    user     = os.getenv("DB_USER", "goadmin"),
    password = os.getenv("DB_PASSWORD", ""),
)

def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return [dict(r) for r in cur.fetchall()]

def serial(o):
    if isinstance(o, decimal.Decimal): return int(o)
    if hasattr(o, "isoformat"): return o.isoformat()
    raise TypeError

def fetch_all(cur):
    cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")

    total_users       = q(cur,"SELECT COUNT(*) AS n FROM pharmacy_users")[0]["n"]
    active_users      = q(cur,"""SELECT COUNT(DISTINCT pharmacy_user_id) AS n
                                  FROM task_pharmacy_connection WHERE is_complete=true""")[0]["n"]
    pharmacy2_opened  = q(cur,"""SELECT COUNT(DISTINCT pharmacy_user_id) AS n
                                  FROM user_pharmacy_connection
                                  WHERE pharmacy_id=13 AND is_open=true""")[0]["n"]
    coins_stats = q(cur,"""SELECT MAX(total_coins) AS max_coins,
                                   ROUND(AVG(total_coins)) AS avg_coins,
                                   PERCENTILE_CONT(0.5) WITHIN GROUP
                                     (ORDER BY total_coins)::int AS median_coins
                             FROM pharmacy_users""")[0]

    reg_by_month = q(cur,"""SELECT TO_CHAR(created_at,'YYYY-MM') AS month, COUNT(*) AS count
                              FROM pharmacy_users GROUP BY month ORDER BY month""")

    leaderboard = q(cur,"""SELECT ROW_NUMBER() OVER (ORDER BY total_coins DESC) AS place,
                                   name, pharmacy_name, total_coins, coins AS current_coins,
                                   avatar_id, created_at::date AS joined
                             FROM pharmacy_users WHERE total_coins>0
                             ORDER BY total_coins DESC LIMIT 20""")

    task_funnel = q(cur,"""SELECT tasks.title, tasks.pharmacy_id,
                                   COUNT(*) AS assigned,
                                   COUNT(CASE WHEN tpc.is_complete THEN 1 END) AS completed
                             FROM task_pharmacy_connection tpc
                             JOIN tasks ON tasks.id=tpc.task_id
                             GROUP BY tasks.id,tasks.title,tasks.pharmacy_id
                             ORDER BY tasks.pharmacy_id,tasks.id""")

    progress_dist = q(cur,"""SELECT
                               COUNT(CASE WHEN tasks_done=0            THEN 1 END) AS no_tasks,
                               COUNT(CASE WHEN tasks_done BETWEEN 1 AND 4  THEN 1 END) AS low,
                               COUNT(CASE WHEN tasks_done BETWEEN 5 AND 14 THEN 1 END) AS mid,
                               COUNT(CASE WHEN tasks_done BETWEEN 15 AND 19 THEN 1 END) AS high,
                               COUNT(CASE WHEN tasks_done=20           THEN 1 END) AS completed_all
                             FROM (SELECT pu.id,
                                          COUNT(CASE WHEN tpc.is_complete THEN 1 END) AS tasks_done
                                    FROM pharmacy_users pu
                                    LEFT JOIN task_pharmacy_connection tpc
                                      ON tpc.pharmacy_user_id=pu.id
                                    GROUP BY pu.id) t""")[0]

    avatars = q(cur,"""SELECT avatar_id, COUNT(*) AS count
                         FROM pharmacy_users GROUP BY avatar_id ORDER BY avatar_id""")

    pharmacy_names = q(cur,"""SELECT pharmacy_name, COUNT(*) AS count
                                FROM pharmacy_users GROUP BY pharmacy_name
                                ORDER BY count DESC LIMIT 15""")

    pharmacy_levels = q(cur,"""SELECT upc.pharmacy_level,
                                       COUNT(DISTINCT upc.pharmacy_user_id) AS users
                                 FROM user_pharmacy_connection upc WHERE upc.pharmacy_id=12
                                 GROUP BY upc.pharmacy_level ORDER BY upc.pharmacy_level""")

    return dict(
        generated_at     = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC"),
        total_users      = int(total_users),
        active_users     = int(active_users),
        pharmacy2_opened = int(pharmacy2_opened),
        coins_stats      = {k: int(v or 0) for k,v in coins_stats.items()},
        reg_by_month     = reg_by_month,
        leaderboard      = leaderboard,
        task_funnel      = task_funnel,
        progress_dist    = progress_dist,
        avatars          = avatars,
        pharmacy_names   = pharmacy_names,
        pharmacy_levels  = pharmacy_levels,
    )

# ─── HTML ────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>OzonGame — Статистика</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0f1117;--card:#1a1d27;--card2:#22263a;
  --accent:#7c5cfc;--accent2:#3ec9a7;--accent3:#f5a623;
  --text:#e8eaf0;--muted:#8890a4;--border:#2d3148;--red:#e05c5c;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif}
header{background:linear-gradient(135deg,#1a0a3d,#0d1a3a);
  padding:24px 32px;display:flex;align-items:center;gap:12px;
  border-bottom:1px solid var(--border)}
header h1{font-size:1.5rem;font-weight:700}
header h1 span{color:var(--accent)}
.badge{background:var(--accent);color:#fff;font-size:.7rem;padding:2px 8px;
  border-radius:20px;font-weight:600;margin-left:8px;vertical-align:middle}
.updated{margin-left:auto;color:var(--muted);font-size:.8rem}
main{max-width:1280px;margin:0 auto;padding:24px}

.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 22px;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.kpi.a::before{background:var(--accent)}.kpi.b::before{background:var(--accent2)}
.kpi.c::before{background:var(--accent3)}.kpi.d::before{background:var(--red)}
.kpi-label{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
.kpi-value{font-size:2rem;font-weight:700;line-height:1}
.kpi-sub{color:var(--muted);font-size:.78rem;margin-top:5px}

.row2{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:18px;margin-bottom:18px}
.row3{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-bottom:18px}
.row-funnel{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}

.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px}
.card h2{font-size:.95rem;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:7px}
.ch{position:relative;height:250px}
.ch.sm{height:200px}

table{width:100%;border-collapse:collapse;font-size:.86rem}
th{color:var(--muted);font-weight:600;text-align:left;padding:9px 12px;
   border-bottom:1px solid var(--border);font-size:.73rem;text-transform:uppercase;letter-spacing:.6px}
td{padding:9px 12px;border-bottom:1px solid #1e2238}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--card2)}
.pl{font-weight:700;color:var(--muted);width:36px}
.pl.g{color:#f5c518}.pl.s{color:#c0c0c0}.pl.b{color:#cd7f32}
.bar-wrap{display:flex;align-items:center;gap:8px}
.bar-inner{height:5px;border-radius:3px;background:var(--accent);opacity:.8}
.num{font-weight:600}.muted{color:var(--muted)}

.leg{display:flex;flex-direction:column;gap:7px;margin-top:14px}
.leg-item{display:flex;align-items:center;gap:8px;font-size:.83rem}
.leg-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.leg-pct{margin-left:auto;color:var(--muted);font-size:.78rem}

@media(max-width:680px){
  .row2,.row3,.row-funnel{grid-template-columns:1fr}
  header{flex-wrap:wrap}.updated{margin-left:0;margin-top:6px}
}
</style>
</head>
<body>
<header>
  <h1>OzonGame <span>Статистика</span><span class="badge">auto-update</span></h1>
  <div class="updated" id="upd"></div>
</header>
<main>

<div class="kpi-row">
  <div class="kpi a"><div class="kpi-label">Пользователей</div>
    <div class="kpi-value" id="k1">—</div>
    <div class="kpi-sub">зарегистрировано за всё время</div></div>
  <div class="kpi b"><div class="kpi-label">Активных</div>
    <div class="kpi-value" id="k2">—</div>
    <div class="kpi-sub">выполнили хотя бы 1 задание</div></div>
  <div class="kpi c"><div class="kpi-label">Открыли аптеку 2</div>
    <div class="kpi-value" id="k3">—</div>
    <div class="kpi-sub">дошли до второй локации</div></div>
  <div class="kpi d"><div class="kpi-label">Макс. монет</div>
    <div class="kpi-value" id="k4">—</div>
    <div class="kpi-sub">среднее <span id="k5">—</span></div></div>
</div>

<div class="row2">
  <div class="card">
    <h2>📅 Регистрации по месяцам</h2>
    <div class="ch"><canvas id="cReg"></canvas></div>
  </div>
  <div class="card">
    <h2>🎯 Вовлечённость</h2>
    <div class="ch sm"><canvas id="cProg"></canvas></div>
    <div class="leg" id="progLeg"></div>
  </div>
</div>

<div class="row-funnel">
  <div class="card">
    <h2>🏪 Воронка заданий — Аптека 1</h2>
    <div class="ch"><canvas id="cF1"></canvas></div>
  </div>
  <div class="card">
    <h2>🏬 Воронка заданий — Аптека 2</h2>
    <div class="ch"><canvas id="cF2"></canvas></div>
  </div>
</div>

<div class="row3">
  <div class="card">
    <h2>⬆️ Уровень аптеки 1</h2>
    <div class="ch sm"><canvas id="cLvl"></canvas></div>
  </div>
  <div class="card">
    <h2>🌸 Названия аптек</h2>
    <div class="ch sm"><canvas id="cNames"></canvas></div>
  </div>
  <div class="card">
    <h2>🧑 Выбор аватара</h2>
    <div class="ch sm"><canvas id="cAv"></canvas></div>
  </div>
</div>

<div class="card">
  <h2>🏆 Топ-20 по монетам</h2>
  <table><thead><tr>
    <th>#</th><th>Имя</th><th>Аптека</th>
    <th>Монет всего</th><th>Монет сейчас</th><th>Дата регистрации</th>
  </tr></thead><tbody id="lb"></tbody></table>
</div>

</main>
<script>
const D=__DATA__;
const $=id=>document.getElementById(id);
const fmt=n=>Number(n).toLocaleString('ru-RU');
const GC='rgba(255,255,255,0.05)',TC='#8890a4';
const PAL=['#7c5cfc','#3ec9a7','#f5a623','#e05c5c','#4a9eff','#e879a0','#f5e642','#9be87a'];

$('upd').textContent='Обновлено: '+D.generated_at;
$('k1').textContent=fmt(D.total_users);
$('k2').textContent=fmt(D.active_users);
$('k3').textContent=fmt(D.pharmacy2_opened);
$('k4').textContent=fmt(D.coins_stats.max_coins);
$('k5').textContent=fmt(D.coins_stats.avg_coins);

function axes(stacked){
  return{x:{grid:{color:GC},ticks:{color:TC,font:{size:11}},stacked:!!stacked},
         y:{grid:{color:GC},ticks:{color:TC,font:{size:11}},beginAtZero:true,stacked:!!stacked}};
}
const tip={backgroundColor:'#22263a',borderColor:'#2d3148',borderWidth:1,
           titleColor:'#e8eaf0',bodyColor:'#8890a4',padding:10};

// Registrations
new Chart($('cReg'),{type:'bar',data:{
  labels:D.reg_by_month.map(r=>r.month),
  datasets:[{data:D.reg_by_month.map(r=>r.count),
    backgroundColor:D.reg_by_month.map(r=>r.count===Math.max(...D.reg_by_month.map(x=>x.count))
      ?'#f5a623cc':'#7c5cfccc'),
    borderRadius:5,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.y)+' чел.'}}},
    scales:axes()}});

// Progress donut
const PD=D.progress_dist;
const pdL=['Не начали (0)','1–4 задания','5–14 заданий','15–19 заданий','Все 20 заданий'];
const pdV=[PD.no_tasks,PD.low,PD.mid,PD.high,PD.completed_all];
const pdC=['#3d4166','#4a9eff','#7c5cfc','#3ec9a7','#f5a623'];
const pdT=pdV.reduce((a,b)=>a+b,0);
new Chart($('cProg'),{type:'doughnut',data:{labels:pdL,
  datasets:[{data:pdV,backgroundColor:pdC,borderWidth:0,hoverOffset:5}]},
  options:{responsive:true,maintainAspectRatio:false,cutout:'66%',
    plugins:{legend:{display:false},tooltip:{...tip,
      callbacks:{label:c=>' '+fmt(c.parsed)+' чел. ('+Math.round(c.parsed/pdT*100)+'%)'}}}
  }});
const leg=$('progLeg');
pdL.forEach((l,i)=>{const p=Math.round(pdV[i]/pdT*100);
  leg.innerHTML+=`<div class="leg-item"><div class="leg-dot" style="background:${pdC[i]}"></div>
    <span>${l}</span><span class="leg-pct">${fmt(pdV[i])} — ${p}%</span></div>`;});

// Funnels
function funnel(id,rows,color){
  const tot=rows[0]?.assigned||1;
  new Chart($(id),{type:'bar',data:{
    labels:rows.map(r=>r.title),
    datasets:[
      {data:rows.map(r=>r.assigned-r.completed),backgroundColor:'#2d3148',
       label:'Не выполнили',stack:'s'},
      {data:rows.map(r=>r.completed),backgroundColor:color+'cc',
       label:'Выполнили',stack:'s'}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',
      plugins:{legend:{display:true,labels:{color:TC,font:{size:10},boxWidth:9}},
        tooltip:{...tip,callbacks:{label:c=>' '+c.dataset.label+': '
          +fmt(c.parsed.x)+' ('+Math.round(c.parsed.x/tot*100)+'%)'}}},
      scales:axes(true)}});
}
funnel('cF1',D.task_funnel.filter(r=>r.pharmacy_id===12),'#7c5cfc');
funnel('cF2',D.task_funnel.filter(r=>r.pharmacy_id===13),'#3ec9a7');

// Levels
new Chart($('cLvl'),{type:'bar',data:{
  labels:D.pharmacy_levels.map(r=>'Уровень '+r.pharmacy_level),
  datasets:[{data:D.pharmacy_levels.map(r=>r.users),
    backgroundColor:D.pharmacy_levels.map((_,i)=>PAL[i%PAL.length]+'bb'),
    borderRadius:5,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.y)+' чел.'}}},
    scales:axes()}});

// Names
new Chart($('cNames'),{type:'bar',data:{
  labels:D.pharmacy_names.map(r=>r.pharmacy_name),
  datasets:[{data:D.pharmacy_names.map(r=>r.count),
    backgroundColor:D.pharmacy_names.map((_,i)=>PAL[i%PAL.length]+'aa'),
    borderRadius:4,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.x)+' чел.'}}},
    scales:axes()}});

// Avatars
new Chart($('cAv'),{type:'doughnut',data:{
  labels:D.avatars.map(r=>'Аватар '+r.avatar_id),
  datasets:[{data:D.avatars.map(r=>r.count),
    backgroundColor:PAL.map(c=>c+'cc'),borderWidth:0,hoverOffset:5}]},
  options:{responsive:true,maintainAspectRatio:false,cutout:'50%',
    plugins:{legend:{display:true,position:'right',labels:{color:TC,font:{size:11},boxWidth:9}},
      tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed)+' чел.'}}}}});

// Leaderboard
const maxC=D.leaderboard[0]?.total_coins||1;
const lb=$('lb');
D.leaderboard.forEach(r=>{
  const cls=r.place===1?'g':r.place===2?'s':r.place===3?'b':'';
  const w=Math.round(r.total_coins/maxC*120);
  lb.innerHTML+=`<tr>
    <td class="pl ${cls}">${r.place}</td>
    <td><strong>${r.name}</strong></td>
    <td>${r.pharmacy_name}</td>
    <td><div class="bar-wrap"><div class="bar-inner" style="width:${w}px"></div>
        <span class="num">${fmt(r.total_coins)}</span></div></td>
    <td class="num">${fmt(r.current_coins)}</td>
    <td class="muted">${r.joined}</td>
  </tr>`;});
</script>
</body>
</html>"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="index.html")
    args = parser.parse_args()

    if not DB["password"]:
        sys.exit("DB_PASSWORD env var is required")

    print(f"Connecting {DB['host']}:{DB['port']}/{DB['dbname']} …")
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        data = fetch_all(cur)
    conn.close()

    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False, default=serial))
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done → {args.output}  [{data['generated_at']}]")

if __name__ == "__main__":
    main()
