#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OzonGame Stats Generator
Runs psql queries via SSH → builds self-contained index.html (no psycopg2 needed).

Env vars:
  SSH_HOST      default: edudev.med-game.ru
  SSH_USER      default: webadmin
  SSH_PASSWORD  required (or use SSH_KEY_FILE)
  DB_NAME       default: medtest2
  DB_USER       default: goadmin
  DB_PASSWORD   default: Visual101
"""

import os, sys, json, argparse, subprocess, textwrap
from datetime import datetime

SSH_HOST = os.getenv("SSH_HOST", "edudev.med-game.ru")
SSH_USER = os.getenv("SSH_USER", "webadmin")
SSH_PASS = os.getenv("SSH_PASSWORD", "MedSite128")
DB_NAME  = os.getenv("DB_NAME",  "medtest2")
DB_USER  = os.getenv("DB_USER",  "goadmin")
DB_PASS  = os.getenv("DB_PASSWORD", "Visual101")


def psql(sql: str) -> list[dict]:
    """Run SQL on the remote server via SSH + psql (stdin), return list of dicts."""
    wrapped = f"SELECT json_agg(t) FROM ({sql.strip().rstrip(';')}) t"
    cmd = [
        "sshpass", "-p", SSH_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{SSH_HOST}",
        f"PGPASSWORD={DB_PASS} psql -h localhost -U {DB_USER} -d {DB_NAME} -t -A"
    ]
    out = subprocess.check_output(cmd, input=wrapped, stderr=subprocess.DEVNULL, text=True).strip()
    if not out or out == "NULL":
        return []
    return json.loads(out)


# Фильтр тестовых: числовой UID = ручная/тестовая регистрация
REAL = "pu.uid !~ '^[0-9]+$'"


def fetch_all() -> dict:
    print("  users …")
    total_users = psql(f"""
        SELECT COUNT(*) AS n FROM pharmacy_users pu WHERE {REAL}
    """)[0]["n"]

    print("  active …")
    active_users = psql(f"""
        SELECT COUNT(DISTINCT tpc.pharmacy_user_id) AS n
        FROM task_pharmacy_connection tpc
        JOIN pharmacy_users pu ON pu.id = tpc.pharmacy_user_id
        WHERE tpc.is_complete=true AND {REAL}
    """)[0]["n"]

    print("  pharmacy2 …")
    pharmacy2_opened = psql(f"""
        SELECT COUNT(DISTINCT upc.pharmacy_user_id) AS n
        FROM user_pharmacy_connection upc
        JOIN pharmacy_users pu ON pu.id = upc.pharmacy_user_id
        WHERE upc.pharmacy_id=13 AND upc.is_open=true AND {REAL}
    """)[0]["n"]

    print("  coins …")
    coins_stats = psql(f"""
        SELECT MAX(total_coins) AS max_coins,
               ROUND(AVG(total_coins)) AS avg_coins,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_coins)::int AS median_coins
        FROM pharmacy_users pu WHERE {REAL}
    """)[0]

    print("  registrations …")
    reg_by_month = psql(f"""
        SELECT TO_CHAR(created_at,'YYYY-MM') AS month, COUNT(*) AS count
        FROM pharmacy_users pu WHERE {REAL} GROUP BY month ORDER BY month
    """)

    print("  leaderboard …")
    leaderboard = psql(f"""
        SELECT ROW_NUMBER() OVER (ORDER BY total_coins DESC) AS place,
               name, pharmacy_name, total_coins, coins AS current_coins,
               avatar_id, created_at::date AS joined
        FROM pharmacy_users pu WHERE total_coins>0 AND {REAL}
        ORDER BY total_coins DESC LIMIT 20
    """)

    print("  task funnel …")
    task_funnel = psql(f"""
        SELECT tasks.title, tasks.pharmacy_id,
               COUNT(*) AS assigned,
               COUNT(CASE WHEN tpc.is_complete THEN 1 END) AS completed
        FROM task_pharmacy_connection tpc
        JOIN tasks ON tasks.id=tpc.task_id
        JOIN pharmacy_users pu ON pu.id=tpc.pharmacy_user_id
        WHERE {REAL}
        GROUP BY tasks.id, tasks.title, tasks.pharmacy_id
        ORDER BY tasks.pharmacy_id, tasks.id
    """)

    print("  progress dist …")
    progress_dist = psql(f"""
        SELECT COUNT(CASE WHEN td=0 THEN 1 END) AS no_tasks,
               COUNT(CASE WHEN td BETWEEN 1 AND 4  THEN 1 END) AS low,
               COUNT(CASE WHEN td BETWEEN 5 AND 14 THEN 1 END) AS mid,
               COUNT(CASE WHEN td BETWEEN 15 AND 19 THEN 1 END) AS high,
               COUNT(CASE WHEN td=20 THEN 1 END) AS completed_all
        FROM (SELECT pu.id,
                     COUNT(CASE WHEN tpc.is_complete THEN 1 END) AS td
              FROM pharmacy_users pu
              LEFT JOIN task_pharmacy_connection tpc ON tpc.pharmacy_user_id=pu.id
              WHERE {REAL}
              GROUP BY pu.id) t
    """)[0]

    print("  avatars …")
    avatars = psql(f"""
        SELECT avatar_id, COUNT(*) AS count
        FROM pharmacy_users pu WHERE {REAL} GROUP BY avatar_id ORDER BY avatar_id
    """)

    print("  pharmacy names …")
    pharmacy_names = psql(f"""
        SELECT pharmacy_name, COUNT(*) AS count
        FROM pharmacy_users pu WHERE {REAL} GROUP BY pharmacy_name ORDER BY count DESC LIMIT 15
    """)

    print("  levels …")
    pharmacy_levels = psql(f"""
        SELECT upc.pharmacy_level, COUNT(DISTINCT upc.pharmacy_user_id) AS users
        FROM user_pharmacy_connection upc
        JOIN pharmacy_users pu ON pu.id=upc.pharmacy_user_id
        WHERE upc.pharmacy_id=12 AND {REAL}
        GROUP BY upc.pharmacy_level ORDER BY upc.pharmacy_level
    """)

    print("  game plays …")
    game_plays_row = psql(f"""
        WITH task_earned AS (
          SELECT tpc.pharmacy_user_id,
                 SUM((tr.params->>'coins')::int) AS coins
          FROM task_pharmacy_connection tpc
          JOIN task_rewards tr ON tr.task_id = tpc.task_id
          WHERE tpc.is_complete = true AND tr.params->>'coins' IS NOT NULL
          GROUP BY tpc.pharmacy_user_id
        )
        SELECT
          SUM(pu.total_coins - COALESCE(te.coins, 0))
            FILTER (WHERE NOT COALESCE((
              SELECT upc2.is_open FROM user_pharmacy_connection upc2
              WHERE upc2.pharmacy_user_id = pu.id AND upc2.pharmacy_id = 13 LIMIT 1
            ), false)) AS ph1_plays,
          SUM(pu.total_coins - COALESCE(te.coins, 0))
            FILTER (WHERE COALESCE((
              SELECT upc2.is_open FROM user_pharmacy_connection upc2
              WHERE upc2.pharmacy_user_id = pu.id AND upc2.pharmacy_id = 13 LIMIT 1
            ), false)) AS ph2_game_coins
        FROM pharmacy_users pu
        LEFT JOIN task_earned te ON te.pharmacy_user_id = pu.id
        WHERE {REAL}
    """)[0]

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
        game_plays_ph1   = int(game_plays_row["ph1_plays"] or 0),
        game_plays_ph2_coins = int(game_plays_row["ph2_game_coins"] or 0),
    )


# ─── HTML ─────────────────────────────────────────────────────────────────────
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
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:18px 22px;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.kpi.a::before{background:var(--accent)}.kpi.b::before{background:var(--accent2)}
.kpi.c::before{background:var(--accent3)}.kpi.d::before{background:var(--red)}
.kpi.e::before{background:#e879a0}
.kpi-label{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
.kpi-value{font-size:2rem;font-weight:700;line-height:1}
.kpi-sub{color:var(--muted);font-size:.78rem;margin-top:5px}

.row2{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:18px;margin-bottom:18px}
.row3{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-bottom:18px}
.row-funnel{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px}
.card h2{font-size:.95rem;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:7px}
.ch{position:relative;height:250px}.ch.sm{height:210px}

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
  <div class="kpi e"><div class="kpi-label">Запуски игр ×1 монета</div>
    <div class="kpi-value" id="k6">—</div>
    <div class="kpi-sub" id="k6sub">—</div></div>
</div>

<div class="row2">
  <div class="card">
    <h2>📅 Регистрации по месяцам</h2>
    <div class="ch"><canvas id="cReg"></canvas></div>
  </div>
  <div class="card">
    <h2>🎯 Вовлечённость пользователей</h2>
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
const tip={backgroundColor:'#22263a',borderColor:'#2d3148',borderWidth:1,
           titleColor:'#e8eaf0',bodyColor:'#8890a4',padding:10};

$('upd').textContent='Обновлено: '+D.generated_at;
$('k1').textContent=fmt(D.total_users);
$('k2').textContent=fmt(D.active_users);
$('k3').textContent=fmt(D.pharmacy2_opened);
$('k4').textContent=fmt(D.coins_stats.max_coins);
$('k5').textContent=fmt(D.coins_stats.avg_coins);
$('k6').textContent='≥ '+fmt(D.game_plays_ph1);
$('k6sub').textContent='точно для аптеки 1 · ещё '+fmt(D.game_plays_ph2_coins)+' монет у игроков аптеки 2';

function axes(s){return{
  x:{grid:{color:GC},ticks:{color:TC,font:{size:11}},stacked:!!s},
  y:{grid:{color:GC},ticks:{color:TC,font:{size:11}},beginAtZero:true,stacked:!!s}};}

new Chart($('cReg'),{type:'bar',data:{
  labels:D.reg_by_month.map(r=>r.month),
  datasets:[{data:D.reg_by_month.map(r=>r.count),
    backgroundColor:D.reg_by_month.map(r=>
      r.count===Math.max(...D.reg_by_month.map(x=>x.count))?'#f5a623cc':'#7c5cfccc'),
    borderRadius:5,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.y)+' чел.'}}},
    scales:axes()}});

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

function funnel(id,rows,color){const tot=rows[0]?.assigned||1;
  new Chart($(id),{type:'bar',data:{labels:rows.map(r=>r.title),datasets:[
      {data:rows.map(r=>r.assigned-r.completed),backgroundColor:'#2d3148',label:'Не выполнили',stack:'s'},
      {data:rows.map(r=>r.completed),backgroundColor:color+'cc',label:'Выполнили',stack:'s'}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',
      plugins:{legend:{display:true,labels:{color:TC,font:{size:10},boxWidth:9}},
        tooltip:{...tip,callbacks:{label:c=>' '+c.dataset.label+': '
          +fmt(c.parsed.x)+' ('+Math.round(c.parsed.x/tot*100)+'%)'}}},
      scales:axes(true)}});}
funnel('cF1',D.task_funnel.filter(r=>r.pharmacy_id===12),'#7c5cfc');
funnel('cF2',D.task_funnel.filter(r=>r.pharmacy_id===13),'#3ec9a7');

new Chart($('cLvl'),{type:'bar',data:{
  labels:D.pharmacy_levels.map(r=>'Уровень '+r.pharmacy_level),
  datasets:[{data:D.pharmacy_levels.map(r=>r.users),
    backgroundColor:D.pharmacy_levels.map((_,i)=>PAL[i%PAL.length]+'bb'),
    borderRadius:5,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.y)+' чел.'}}},
    scales:axes()}});

new Chart($('cNames'),{type:'bar',data:{
  labels:D.pharmacy_names.map(r=>r.pharmacy_name),
  datasets:[{data:D.pharmacy_names.map(r=>r.count),
    backgroundColor:D.pharmacy_names.map((_,i)=>PAL[i%PAL.length]+'aa'),
    borderRadius:4,borderSkipped:false}]},
  options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed.x)+' чел.'}}},
    scales:axes()}});

new Chart($('cAv'),{type:'doughnut',data:{
  labels:D.avatars.map(r=>'Аватар '+r.avatar_id),
  datasets:[{data:D.avatars.map(r=>r.count),
    backgroundColor:PAL.map(c=>c+'cc'),borderWidth:0,hoverOffset:5}]},
  options:{responsive:true,maintainAspectRatio:false,cutout:'50%',
    plugins:{legend:{display:true,position:'right',labels:{color:TC,font:{size:11},boxWidth:9}},
      tooltip:{...tip,callbacks:{label:c=>' '+fmt(c.parsed)+' чел.'}}}}});

const maxC=D.leaderboard[0]?.total_coins||1;
const lb=$('lb');
D.leaderboard.forEach(r=>{
  const cls=r.place===1?'g':r.place===2?'s':r.place===3?'b':'';
  const w=Math.round(r.total_coins/maxC*120);
  lb.innerHTML+=`<tr>
    <td class="pl ${cls}">${r.place}</td><td><strong>${r.name}</strong></td>
    <td>${r.pharmacy_name}</td>
    <td><div class="bar-wrap"><div class="bar-inner" style="width:${w}px"></div>
        <span class="num">${fmt(r.total_coins)}</span></div></td>
    <td class="num">${fmt(r.current_coins)}</td>
    <td class="muted">${r.joined}</td></tr>`;});
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="index.html")
    args = parser.parse_args()

    # check sshpass
    if subprocess.call(["which","sshpass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        sys.exit("sshpass not found. Install: brew install hudochenkov/sshpass/sshpass  OR  apt install sshpass")

    print(f"Fetching data from {SSH_USER}@{SSH_HOST} → {DB_NAME} …")
    data = fetch_all()

    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done → {args.output}  [{data['generated_at']}]")


if __name__ == "__main__":
    main()
