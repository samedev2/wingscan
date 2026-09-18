"use strict";

const COMPORTAMENTOS = ["comendo", "bebendo", "bicando", "andando", "descansando", "agitado"];
const ROTULO_TIPO = { sistema: "Sistema", entrada: "Entrada", comportamento: "Comportam.", alerta: "Alerta" };
const MAX_LOG = 1500;

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
const $$ = (seletor, raiz = document) => [...raiz.querySelectorAll(seletor)];

const CORES_CLASSE = { pinto: "--classe-pinto", galinha: "--classe-galinha", galo: "--classe-galo" };

const app = {
  info: null,
  status: { rodando: false },
  ultimoSeq: 0,
  ultimo: null, // { estado, imagem } do último quadro desenhado
  tokenImagem: 0,
  pausado: false,
  ultimaTabela: 0,
  mostrarComportamento: true,
  revisaoAoVivo: false,
  deteccaoSobMouse: null,
  confirmadasNaSessao: 0,
};

// ---------------------------------------------------------------------------
// utilitários
// ---------------------------------------------------------------------------
function duracao(segundos) {
  const s = Math.max(0, Math.floor(segundos));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

function tempoVideo(t) {
  const m = Math.floor(t / 60);
  return `${String(m).padStart(2, "0")}:${(t - m * 60).toFixed(1).padStart(4, "0")}`;
}

function esc(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function cssVar(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function guardar(chave, valor) {
  try { localStorage.setItem(`pinteiro.${chave}`, valor); } catch { /* armazenamento indisponível */ }
}

function lembrar(chave) {
  try { return localStorage.getItem(`pinteiro.${chave}`); } catch { return null; }
}

// ---------------------------------------------------------------------------
// conexão em tempo real (SSE)
// ---------------------------------------------------------------------------
function conectar() {
  const fonte = new EventSource("/api/eventos");
  fonte.onopen = () => definirConexao(true);
  fonte.onerror = () => definirConexao(false);
  fonte.addEventListener("historico", (e) => {
    $("#log-lista").innerHTML = "";
    app.ultimoSeq = 0;
    for (const ev of JSON.parse(e.data)) adicionarLog(ev, false);
    rolarLogParaFim();
  });
  fonte.addEventListener("log", (e) => adicionarLog(JSON.parse(e.data), true));
  fonte.addEventListener("status", (e) => aplicarStatus(JSON.parse(e.data)));
  fonte.addEventListener("estado", (e) => receberEstado(JSON.parse(e.data)));
}

function definirConexao(ok) {
  const badge = $("#badge-conexao");
  badge.textContent = ok ? "Conectado" : "Sem conexão com o servidor";
  badge.classList.toggle("erro", !ok);
}

function aplicarStatus(st) {
  const eraRodando = app.status.rodando;
  app.status = st;
  const badge = $("#badge-rodando");
  badge.textContent = st.rodando ? "Monitorando" : "Parado";
  badge.classList.toggle("ativo", st.rodando);
  const desde = st.desde ? st.desde.slice(11, 16) : "";
  $("#fonte-desc").textContent = st.rodando
    ? `${st.fonte} · desde ${desde}`
    : st.fonte ? `Última fonte: ${st.fonte}` : "Nenhuma fonte ativa";
  $("#btn-parar").disabled = !st.rodando;
  $("#btn-iniciar").textContent = st.rodando ? "↻ Reiniciar" : "▶ Iniciar";
  $("#tela").classList.toggle("parada", !st.rodando && !!app.ultimo);
  if (st.rodando && !eraRodando) {
    app.ultimo = null;
    $("#tela-vazia").hidden = false;
    $("#tela-vazia").textContent = "Aguardando o primeiro quadro…";
  }
}

// ---------------------------------------------------------------------------
// log
// ---------------------------------------------------------------------------
function logEstaNoFim() {
  const lista = $("#log-lista");
  return lista.scrollHeight - lista.scrollTop - lista.clientHeight < 40;
}

function rolarLogParaFim() {
  const lista = $("#log-lista");
  lista.scrollTop = lista.scrollHeight;
  $("#btn-novos").hidden = true;
}

function correspondeBusca(li) {
  const termo = $("#busca").value.trim().toLowerCase();
  return !termo || li.textContent.toLowerCase().includes(termo);
}

function adicionarLog(ev, aoVivo) {
  if (ev.seq <= app.ultimoSeq) return;
  app.ultimoSeq = ev.seq;

  const lista = $("#log-lista");
  const noFim = logEstaNoFim();
  const li = document.createElement("li");
  li.className = `ev tipo-${ev.tipo} nivel-${ev.nivel}`;
  li.innerHTML = `<time>${esc(ev.ts.slice(11, 23))}</time><span class="tag">${esc(ROTULO_TIPO[ev.tipo] || ev.tipo)}</span><span class="msg"></span>`;
  $(".msg", li).textContent = ev.msg;
  li.evento = ev;
  li.hidden = !correspondeBusca(li);
  lista.appendChild(li);
  while (lista.childElementCount > MAX_LOG) lista.firstElementChild.remove();
  $("#log-contador").textContent = `${lista.childElementCount} eventos`;

  if (!aoVivo) return;
  if (!app.pausado && noFim) rolarLogParaFim();
  else $("#btn-novos").hidden = false;
}

function alternarDetalhes(li) {
  const existente = $("pre", li);
  if (existente) return existente.remove();
  const dados = li.evento?.dados;
  if (!dados || !Object.keys(dados).length) return;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(dados, null, 2);
  li.appendChild(pre);
}

function configurarLog() {
  const lista = $("#log-lista");
  lista.addEventListener("click", (e) => {
    if (e.target.closest("pre") || getSelection().toString()) return;
    const li = e.target.closest(".ev");
    if (li) alternarDetalhes(li);
  });
  lista.addEventListener("scroll", () => {
    if (logEstaNoFim()) $("#btn-novos").hidden = true;
  });

  for (const caixa of $$("[data-filtro]")) {
    caixa.addEventListener("change", () => lista.classList.toggle(`ocultar-${caixa.dataset.filtro}`, !caixa.checked));
  }
  $("#filtro-debug").addEventListener("change", (e) => lista.classList.toggle("ocultar-debug", !e.target.checked));

  let espera;
  $("#busca").addEventListener("input", () => {
    clearTimeout(espera);
    espera = setTimeout(() => {
      for (const li of lista.children) li.hidden = !correspondeBusca(li);
    }, 150);
  });

  $("#btn-pausar").addEventListener("click", (e) => {
    app.pausado = !app.pausado;
    e.currentTarget.textContent = app.pausado ? "Retomar" : "Pausar";
    e.currentTarget.classList.toggle("ligado", app.pausado);
    if (!app.pausado) rolarLogParaFim();
  });
  $("#btn-limpar").addEventListener("click", () => {
    lista.innerHTML = "";
    $("#log-contador").textContent = "0 eventos";
    $("#btn-novos").hidden = true;
  });
  $("#btn-novos").addEventListener("click", rolarLogParaFim);
}

// ---------------------------------------------------------------------------
// estado ao vivo: KPIs, alertas, tabela e canvas
// ---------------------------------------------------------------------------
function receberEstado(estado) {
  if (estado.imagem) {
    const token = ++app.tokenImagem;
    const img = new Image();
    img.onload = () => {
      if (token === app.tokenImagem) desenhar(estado, img);
    };
    img.src = `data:image/jpeg;base64,${estado.imagem}`;
  } else {
    app.tokenImagem++;
    desenhar(estado, null);
  }
  $("#tela-vazia").hidden = true;
  $("#tela").classList.toggle("parada", !app.status.rodando);

  let info = `frame ${estado.frame} · t ${tempoVideo(estado.t)} · ${estado.fps} fps`;
  if (estado.inferencia_ms) info += ` · inferência ${Math.round(estado.inferencia_ms)} ms`;
  $("#entrada-info").textContent = info;

  atualizarKpis(estado);
  atualizarFaixaAlertas(estado);
  const agora = performance.now();
  if (agora - app.ultimaTabela > 400) {
    app.ultimaTabela = agora;
    renderizarTabela(estado);
  }
}

function atualizarKpis(estado) {
  const valores = { detectados: estado.detectados, alertas: estado.alertas_ativos, ...estado.por_comportamento };
  for (const el of $$("[data-kpi]")) {
    const v = valores[el.dataset.kpi];
    el.textContent = v ?? "–";
  }
  $(".kpi-alerta").classList.toggle("ativo", estado.alertas_ativos > 0);
  const plural = { pinto: "pintos", galinha: "galinhas", galo: "galos", ave: "aves" };
  const partes = Object.entries(estado.por_classe || {}).map(([c, n]) => `${n} ${n === 1 ? c : plural[c] || c}`);
  $("#kpi-classes").textContent = partes.length ? partes.join(" · ") : "Aves detectadas";
}

function atualizarFaixaAlertas(estado) {
  const chips = [];
  if (estado.grupo_agitado) chips.push("⚠ Lote agitado");
  for (const p of estado.pintos) {
    for (const alerta of p.alertas) chips.push(`⚠ #${p.id} ${alerta.texto}`);
  }
  const faixa = $("#faixa-alertas");
  faixa.hidden = chips.length === 0;
  faixa.innerHTML = chips.map((c) => `<span class="alerta-chip">${esc(c)}</span>`).join("");
}

function renderizarTabela(estado) {
  const corpo = $("#tabela tbody");
  const visiveis = estado.pintos.filter((p) => p.visivel).length;
  $("#qtd-pintos").textContent = estado.pintos.length ? `${visiveis} visíveis · ${estado.pintos.length} rastreados` : "";
  if (!estado.pintos.length) {
    corpo.innerHTML = `<tr><td colspan="7" class="vazio">Nenhuma ave rastreada ainda.</td></tr>`;
    return;
  }
  corpo.innerHTML = estado.pintos.map((p) => {
    const alertas = new Set(p.alertas.map((a) => a.chave));
    const barra = COMPORTAMENTOS
      .filter((c) => p.pct[c] > 0.005)
      .map((c) => `<i style="width:${(p.pct[c] * 100).toFixed(1)}%;background:var(--c-${c})" title="${c} ${Math.round(p.pct[c] * 100)}%"></i>`)
      .join("");
    const status = p.alertas.length
      ? `<span class="status-alerta">⚠ ${esc(p.alertas.map((a) => a.texto).join(" · "))}</span>`
      : `<span class="status-ok">OK</span>`;
    const classes = [p.alertas.length ? "com-alerta" : "", p.visivel ? "" : "fora"].join(" ");
    return `<tr class="${classes}">
      <td><span class="id-pinto">#${esc(p.id)}</span>${p.classe && p.classe !== "pinto" ? `<span class="tipo-ave">${esc(p.classe)}</span>` : ""}${p.visivel ? "" : '<span class="fora-tag">fora de vista</span>'}</td>
      <td>${p.estado ? `<span class="estado" style="--c:var(--c-${esc(p.estado)})">${esc(p.estado)}</span>` : "–"}</td>
      <td class="${alertas.has("imovel") ? "critico" : ""}">${duracao(p.estado_ha_s)}</td>
      <td class="${alertas.has("sem_comer") ? "critico" : ""}">${duracao(p.sem_comer_s)}</td>
      <td class="${alertas.has("sem_beber") ? "critico" : ""}">${duracao(p.sem_beber_s)}</td>
      <td><div class="barra">${barra}</div></td>
      <td>${status}</td>
    </tr>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// canvas
// ---------------------------------------------------------------------------
const GRAOS = Array.from({ length: 320 }, () => ({
  x: Math.random(), y: Math.random(), c: 2 + Math.random() * 5, a: Math.random() * Math.PI, o: 0.15 + Math.random() * 0.35,
}));

function desenhar(estado, imagem) {
  app.ultimo = { estado, imagem };
  const tela = $("#tela");
  const canvas = $("#canvas");
  const [largura, altura] = estado.dim;
  const proporcao = (largura / altura).toFixed(4);
  if (tela.style.getPropertyValue("--proporcao") !== proporcao) tela.style.setProperty("--proporcao", proporcao);

  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  if (!W || !H) return;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const demo = !imagem;
  if (imagem) ctx.drawImage(imagem, 0, 0, W, H);
  else desenharPiso(ctx, W, H);

  desenharZonas(ctx, W, H, demo);

  const comAlerta = new Set(estado.pintos.filter((p) => p.alertas.length).map((p) => p.id));
  for (const d of estado.deteccoes) {
    desenharDeteccao(ctx, d, W, H, comAlerta.has(d.id), demo);
    if (app.revisaoAoVivo && app.deteccaoSobMouse && d.id != null && d.id === app.deteccaoSobMouse.id) desenharRealceRevisao(ctx, d, W, H);
  }
}

function desenharRealceRevisao(ctx, d, W, H) {
  const [x1, y1, x2, y2] = [d.caixa[0] * W, d.caixa[1] * H, d.caixa[2] * W, d.caixa[3] * H];
  ctx.save();
  ctx.strokeStyle = cssVar("--primaria");
  ctx.lineWidth = 3;
  ctx.setLineDash([]);
  ctx.strokeRect(x1 - 3, y1 - 3, x2 - x1 + 6, y2 - y1 + 6);
  ctx.restore();
}

function desenharPiso(ctx, W, H) {
  ctx.fillStyle = cssVar("--piso");
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = cssVar("--piso-grao");
  for (const g of GRAOS) {
    ctx.save();
    ctx.globalAlpha = g.o;
    ctx.translate(g.x * W, g.y * H);
    ctx.rotate(g.a);
    ctx.fillRect(-g.c / 2, -0.8, g.c, 1.6);
    ctx.restore();
  }
  ctx.strokeStyle = cssVar("--parede");
  ctx.lineWidth = 8;
  ctx.strokeRect(4, 4, W - 8, H - 8);
}

function caminhoArredondado(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
}

function desenharZonas(ctx, W, H, demo) {
  const zonas = app.info?.zonas;
  if (!zonas) return;
  const estilos = { comedouro: ["--c-comendo", "COMEDOURO"], bebedouro: ["--c-bebendo", "BEBEDOURO"] };
  for (const [nome, retangulos] of Object.entries(zonas)) {
    const [variavel, rotulo] = estilos[nome] || ["--sutil", nome.toUpperCase()];
    const cor = cssVar(variavel);
    for (const [x1, y1, x2, y2] of retangulos) {
      const x = x1 * W, y = y1 * H, w = (x2 - x1) * W, h = (y2 - y1) * H;
      caminhoArredondado(ctx, x, y, w, h, 8);
      ctx.globalAlpha = demo ? 0.22 : 0.12;
      ctx.fillStyle = cor;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = cor;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "700 10px system-ui, sans-serif";
      ctx.fillStyle = cor;
      ctx.fillText(rotulo, x + 6, y + 14);
    }
  }
}

function desenharPintinho(ctx, cx, cy, r, comportamento) {
  const cabecaBaixa = comportamento === "comendo" || comportamento === "bicando" || comportamento === "bebendo";
  ctx.save();
  ctx.translate(cx, cy);
  // corpo
  ctx.beginPath();
  ctx.ellipse(0, 0, r, r * (comportamento === "descansando" ? 0.72 : 0.85), 0, 0, Math.PI * 2);
  ctx.fillStyle = "#f6cf3f";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "#c99a17";
  ctx.stroke();
  // cabeça
  const hx = r * 0.62, hy = cabecaBaixa ? -r * 0.05 : -r * 0.5, hr = r * 0.46;
  ctx.beginPath();
  ctx.arc(hx, hy, hr, 0, Math.PI * 2);
  ctx.fillStyle = "#fad64f";
  ctx.fill();
  ctx.stroke();
  // bico
  ctx.beginPath();
  ctx.moveTo(hx + hr * 0.85, hy - hr * 0.2);
  ctx.lineTo(hx + hr * 1.55, hy + hr * 0.1);
  ctx.lineTo(hx + hr * 0.85, hy + hr * 0.35);
  ctx.closePath();
  ctx.fillStyle = "#e8862a";
  ctx.fill();
  // olho
  ctx.beginPath();
  ctx.arc(hx + hr * 0.3, hy - hr * 0.2, Math.max(1.2, hr * 0.14), 0, Math.PI * 2);
  ctx.fillStyle = "#2b2b2b";
  ctx.fill();
  ctx.restore();
}

// A moldura é só identificação (UID + classe) — não muda de cor com o comportamento.
// Comportamento vira uma "flag" à parte, presa no canto da moldura, e some com o filtro.
function desenharDeteccao(ctx, d, W, H, alerta, demo) {
  const [x1, y1, x2, y2] = [d.caixa[0] * W, d.caixa[1] * H, d.caixa[2] * W, d.caixa[3] * H];
  const w = x2 - x1, h = y2 - y1;
  const corId = cssVar(CORES_CLASSE[d.classe] || "--sutil");
  const corMoldura = alerta ? cssVar("--vermelho") : corId;

  if (demo) desenharPintinho(ctx, x1 + w / 2, y1 + h / 2, Math.min(w, h) * 0.36, d.comportamento);

  ctx.lineWidth = alerta ? 2.5 : 1.8;
  ctx.strokeStyle = corMoldura;
  ctx.setLineDash(alerta ? [5, 3] : []);
  ctx.strokeRect(x1, y1, w, h);
  ctx.setLineDash([]);

  const idTexto = `${d.id != null ? `#${d.id}` : "?"}${alerta ? " ⚠" : ""}`;
  ctx.font = "600 11px system-ui, sans-serif";
  const idW = ctx.measureText(idTexto).width + 8;
  const idY = y1 >= 17 ? y1 - 16 : y2 + 1;
  const idX = Math.min(Math.max(0, x1), W - idW);
  ctx.fillStyle = corMoldura;
  caminhoArredondado(ctx, idX, idY, idW, 15, 3);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.fillText(idTexto, idX + 4, idY + 11);

  if (app.mostrarComportamento) desenharFlagComportamento(ctx, d, x1, y1, x2, y2, W, idX, idW, idY);
}

const LARGURA_MINIMA_FLAG = 70; // caixa mais estreita que isso não cabe o texto sem invadir o ID vizinho

// Bandeirinha presa no canto oposto ao rótulo de ID, com um bico triangular apontando pra caixa.
// Em caixas pequenas e apinhadas (pintinhos amontoados), vira só um pontinho colorido — o texto
// largo, nesse caso, encostaria no ID de uma ave vizinha, que tem prioridade sobre o comportamento.
function desenharFlagComportamento(ctx, d, x1, y1, x2, y2, W, idX, idW, idY) {
  const nome = d.comportamento || d.rotulo;
  if (!nome) return;
  const cor = cssVar(`--c-${nome}`) || "#999";

  if (x2 - x1 < LARGURA_MINIMA_FLAG) {
    ctx.fillStyle = cor;
    ctx.beginPath();
    ctx.arc(x2, y1, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
    return;
  }

  const texto = d.classe && d.classe !== "pinto" ? `${d.classe} · ${nome}` : nome;
  ctx.font = "600 11px system-ui, sans-serif";
  const tw = ctx.measureText(texto).width + 8;
  const th = 15;
  const fx = Math.min(Math.max(0, x2 - tw), W - tw);
  const sobrepoe = fx < idX + idW && fx + tw > idX; // mesma linha que o rótulo de ID e esbarra nele
  const fy = sobrepoe ? (idY === y1 - 16 ? y2 + 1 : y1 - 16) : idY;

  ctx.fillStyle = cor;
  caminhoArredondado(ctx, fx, fy, tw, th, 3);
  ctx.fill();
  // bico triangular do lado da caixa, dando a sensação de bandeirinha presa na moldura
  const bicoX = fx + tw < x2 ? fx + tw : fx;
  ctx.beginPath();
  ctx.moveTo(bicoX, fy + 3);
  ctx.lineTo(bicoX + (fx + tw < x2 ? 5 : -5), fy + th / 2);
  ctx.lineTo(bicoX, fy + th - 3);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.fillText(texto, fx + 4, fy + 11);
}

// ---------------------------------------------------------------------------
// revisão ao vivo: clique confirma a classe da caixa, tecla 1/2/3 corrige —
// cada ação salva aquele quadro como dado de treino já revisado (ver monitor/treino.py)
// ---------------------------------------------------------------------------
function deteccaoNoPonto(px, py) {
  const estado = app.ultimo?.estado;
  if (!estado?.imagem) return null; // demo não tem imagem real pra salvar
  const canvas = $("#canvas");
  const W = canvas.clientWidth, H = canvas.clientHeight;
  for (let i = estado.deteccoes.length - 1; i >= 0; i--) {
    const d = estado.deteccoes[i];
    const [x1, y1, x2, y2] = [d.caixa[0] * W, d.caixa[1] * H, d.caixa[2] * W, d.caixa[3] * H];
    if (px >= x1 && px <= x2 && py >= y1 && py <= y2) return d;
  }
  return null;
}

function mostrarFlashRevisao(texto, ok = true) {
  const el = $("#revisao-flash");
  el.textContent = texto;
  el.className = ok ? "revisao-flash ok" : "revisao-flash erro";
  el.hidden = false;
  clearTimeout(mostrarFlashRevisao._t);
  mostrarFlashRevisao._t = setTimeout(() => { el.hidden = true; }, 1200);
}

async function confirmarDeteccao(d, classeForcada) {
  const imagem = app.ultimo?.estado?.imagem;
  const classe = classeForcada || d.classe;
  if (!imagem || !classe) return;
  const [x1, y1, x2, y2] = d.caixa;
  try {
    const resposta = await fetch("/api/treino/confirmar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ imagem, caixa: { x1, y1, x2, y2 }, classe }),
    });
    const corpo = await resposta.json();
    if (!corpo.ok) return mostrarFlashRevisao(corpo.erro || "Falha ao salvar", false);
    app.confirmadasNaSessao++;
    $("#revisao-contador").textContent = `${app.confirmadasNaSessao} confirmadas`;
    mostrarFlashRevisao(`✓ ${d.id != null ? `#${d.id} ` : ""}${classe}`);
  } catch {
    mostrarFlashRevisao("Não foi possível falar com o servidor", false);
  }
}

function configurarRevisaoAoVivo() {
  const canvas = $("#canvas");
  const caixa = $("#revisao-ao-vivo");
  caixa.checked = lembrar("revisaoAoVivo") === "1";
  app.revisaoAoVivo = caixa.checked;
  $("#revisao-dica").hidden = !caixa.checked;
  caixa.addEventListener("change", () => {
    app.revisaoAoVivo = caixa.checked;
    guardar("revisaoAoVivo", caixa.checked ? "1" : "0");
    $("#revisao-dica").hidden = !caixa.checked;
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!app.revisaoAoVivo) return;
    const rect = canvas.getBoundingClientRect();
    app.deteccaoSobMouse = deteccaoNoPonto(e.clientX - rect.left, e.clientY - rect.top);
    canvas.classList.toggle("clicavel", !!app.deteccaoSobMouse);
  });
  canvas.addEventListener("mouseleave", () => { app.deteccaoSobMouse = null; });

  canvas.addEventListener("click", (e) => {
    if (!app.revisaoAoVivo) return;
    const rect = canvas.getBoundingClientRect();
    const d = deteccaoNoPonto(e.clientX - rect.left, e.clientY - rect.top);
    if (d) confirmarDeteccao(d);
  });

  window.addEventListener("keydown", (e) => {
    if (!app.revisaoAoVivo || !app.deteccaoSobMouse) return;
    const indice = { "1": "pinto", "2": "galinha", "3": "galo" }[e.key];
    if (indice) confirmarDeteccao(app.deteccaoSobMouse, indice);
  });
}

// ---------------------------------------------------------------------------
// controles
// ---------------------------------------------------------------------------
async function carregarInfo() {
  try {
    const resposta = await fetch("/api/info");
    app.info = await resposta.json();
  } catch {
    return;
  }
  const seletor = $("#video");
  const atual = seletor.value || lembrar("video");
  seletor.innerHTML = app.info.videos.length
    ? app.info.videos.map((v) => `<option${v === atual ? " selected" : ""}>${esc(v)}</option>`).join("")
    : `<option value="">(nenhum vídeo em videos/)</option>`;
  if (!app.status.desde) aplicarStatus(app.info.status);
  atualizarProntidao();
}

function atualizarProntidao() {
  const tipo = $("#tipo").value;
  for (const campo of $$(".campo[data-tipo]")) campo.hidden = campo.dataset.tipo !== tipo;
  const alvo = $("#prontidao");
  if (tipo === "demo") {
    alvo.innerHTML = `<span class="ok">Não precisa de câmera nem de modelo: simula um lote com alguns pintos doentes e alertas com tempos curtos.</span>`;
    return;
  }
  const info = app.info;
  if (!info) {
    alvo.innerHTML = "";
    return;
  }
  const item = (ok, texto, opcional = false) => `<span class="${ok ? "ok" : opcional ? "opcional" : "falta"}">${esc(texto)}</span>`;
  alvo.innerHTML = [
    item(info.dependencias.cv2, "OpenCV"),
    item(info.dependencias.ultralytics, "Ultralytics YOLO"),
    item(info.modelos.deteccao.existe, `Modelo de detecção (${info.modelos.deteccao.caminho})`),
    info.modelos.comportamento.caminho
      ? item(info.modelos.comportamento.existe,
        `Modelo de comportamento (${info.modelos.comportamento.caminho})${info.modelos.comportamento.existe ? "" : " — opcional, sem ele usa zonas + movimento"}`, true)
      : item(false, "Comportamento por zonas + movimento (modelo de comportamento desligado)", true),
  ].join("");
}

function avisar(texto) {
  $("#aviso-controle").textContent = texto || "";
}

async function iniciar() {
  const tipo = $("#tipo").value;
  const pedido = { tipo };
  if (tipo === "arquivo") {
    pedido.nome = $("#video").value;
    if (!pedido.nome) return avisar("Envie um vídeo ou coloque um arquivo na pasta videos/.");
    guardar("video", pedido.nome);
  } else if (tipo === "webcam") {
    pedido.indice = Number($("#indice").value || 0);
    guardar("indice", pedido.indice);
  } else if (tipo === "rtsp") {
    pedido.url = $("#url").value.trim();
    if (!pedido.url) return avisar("Informe a URL da câmera.");
    guardar("url", pedido.url);
  }
  avisar("");
  const botao = $("#btn-iniciar");
  botao.disabled = true;
  try {
    const resposta = await fetch("/api/iniciar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pedido),
    });
    const corpo = await resposta.json();
    if (!corpo.ok) avisar(corpo.erro);
  } catch {
    avisar("Não foi possível falar com o servidor.");
  } finally {
    botao.disabled = false;
  }
}

async function parar() {
  $("#btn-parar").disabled = true;
  try {
    await fetch("/api/parar", { method: "POST" });
  } catch {
    avisar("Não foi possível falar com o servidor.");
  }
}

function enviarVideo(arquivo) {
  const progresso = $("#upload-progresso");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.setRequestHeader("X-Nome-Arquivo", encodeURIComponent(arquivo.name));
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) progresso.textContent = `enviando ${Math.round((e.loaded / e.total) * 100)}%`;
  };
  xhr.onload = async () => {
    let corpo = {};
    try { corpo = JSON.parse(xhr.responseText); } catch { /* resposta inválida */ }
    if (!corpo.ok) {
      progresso.textContent = "";
      return avisar(corpo.erro || "Falha no envio do vídeo.");
    }
    progresso.textContent = "enviado ✓";
    guardar("video", corpo.nome);
    $("#video").value = "";
    await carregarInfo();
    $("#video").value = corpo.nome;
  };
  xhr.onerror = () => {
    progresso.textContent = "";
    avisar("Falha no envio do vídeo.");
  };
  avisar("");
  xhr.send(arquivo);
}

function configurarFiltroComportamento() {
  const caixa = $("#filtro-comportamento");
  caixa.checked = lembrar("mostrarComportamento") !== "0";
  app.mostrarComportamento = caixa.checked;
  caixa.addEventListener("change", () => {
    app.mostrarComportamento = caixa.checked;
    guardar("mostrarComportamento", caixa.checked ? "1" : "0");
    if (app.ultimo) desenhar(app.ultimo.estado, app.ultimo.imagem);
  });
}

function configurarControles() {
  const tipo = $("#tipo");
  tipo.value = lembrar("tipo") || "demo";
  $("#indice").value = lembrar("indice") || 0;
  $("#url").value = lembrar("url") || "";
  tipo.addEventListener("change", () => {
    guardar("tipo", tipo.value);
    avisar("");
    atualizarProntidao();
    if (tipo.value !== "demo") carregarInfo();
  });
  $("#btn-iniciar").addEventListener("click", iniciar);
  $("#btn-parar").addEventListener("click", parar);
  $("#upload").addEventListener("change", (e) => {
    const arquivo = e.target.files[0];
    if (arquivo) enviarVideo(arquivo);
    e.target.value = "";
  });
  atualizarProntidao();
}

// ---------------------------------------------------------------------------
configurarControles();
configurarFiltroComportamento();
configurarRevisaoAoVivo();
configurarLog();
carregarInfo().then(conectar);
new ResizeObserver(() => app.ultimo && desenhar(app.ultimo.estado, app.ultimo.imagem)).observe($("#tela"));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => app.ultimo && desenhar(app.ultimo.estado, app.ultimo.imagem));
