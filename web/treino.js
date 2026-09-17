"use strict";

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
const $$ = (seletor, raiz = document) => [...raiz.querySelectorAll(seletor)];
const CORES = { pinto: "--classe-pinto", galinha: "--classe-galinha", galo: "--classe-galo" };
const MIN_CAIXA = 0.012;

const estado = {
  imagens: [], // [{nome, revisado, caixas}]
  atual: null, // nome
  caixas: [],
  selecionada: -1,
  classeAtual: "pinto",
  imagemEl: null,
  arrasto: null,
  sujo: false,
  zoom: 1,
  centroZoom: { x: 0.5, y: 0.5 },
};

function cssVar(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

// ---------------------------------------------------------------------------
// carregamento de dados
// ---------------------------------------------------------------------------
async function carregarResumo() {
  const r = await fetch("/api/treino/resumo");
  const dados = await r.json();
  estado.imagens = dados.imagens;
  renderizarVideos(dados.videos);
  renderizarLista();
  renderizarEstatisticas(dados.estatisticas);
  atualizarStatusTreino(dados.job);
  return dados;
}

function renderizarEstatisticas(stats) {
  const alvo = $("#t-stats");
  if (!stats) return;
  const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(2)}%`);
  const linhas = ["pinto", "galinha", "galo"].map((c) => {
    const s = stats[c] || { n: 0 };
    return `<tr><td>${c}</td><td>${s.n}</td><td class="faixa">${pct(s.area_min)} – ${pct(s.area_media)} – ${pct(s.area_max)}</td></tr>`;
  }).join("");
  const p = stats.pinto, g = stats.galinha;
  let aviso = "";
  if (p?.n && g?.n) {
    const sobrepoe = g.area_min <= p.area_max;
    aviso = sobrepoe
      ? `<p class="sobrepoe">✓ Já há galinhas do tamanho de pintos rotuladas — bom sinal.</p>`
      : `<p class="nao-sobrepoe">✗ Nenhuma galinha revisada é tão pequena quanto um pinto ainda — revise galinhas mais distantes.</p>`;
  }
  alvo.innerHTML = `<table><thead><tr><th>Classe</th><th>N</th><th>menor – média – maior</th></tr></thead><tbody>${linhas}</tbody></table>${aviso}`;
}

function renderizarVideos(videos) {
  const alvo = $("#t-videos");
  if (!videos.length) {
    alvo.textContent = "Nenhum vídeo em videos/ ainda. Envie um pelo painel principal.";
    return;
  }
  alvo.innerHTML = videos.map((v) => `<label><input type="checkbox" value="${v}" checked> ${v}</label>`).join("");
}

function filtroAtual() {
  return $("#t-filtro").value;
}

function listaFiltrada() {
  const f = filtroAtual();
  let itens = estado.imagens;
  if (f === "pendentes") itens = itens.filter((i) => !i.revisado);
  else if (f === "revisados") itens = itens.filter((i) => i.revisado);
  if ($("#t-ordenar").value === "pequenas") {
    itens = [...itens].sort((a, b) => (a.menor_area ?? 1) - (b.menor_area ?? 1));
  }
  return itens;
}

function renderizarLista() {
  const itens = listaFiltrada();
  $("#t-contador").textContent = `${estado.imagens.filter((i) => i.revisado).length}/${estado.imagens.length} revisados`;
  const lista = $("#t-lista");
  lista.innerHTML = itens.map((i) => `
    <li data-nome="${i.nome}" class="${i.revisado ? "revisado" : ""} ${i.nome === estado.atual ? "ativo" : ""}">
      <span class="ponto-estado"></span>
      <span class="nome-quadro">${i.nome}</span>
      <span class="n-caixas">${i.caixas}</span>
    </li>`).join("") || `<li class="sutil" style="cursor:default">Nada aqui ainda.</li>`;
  for (const li of $$("li[data-nome]", lista)) {
    li.addEventListener("click", () => abrirImagem(li.dataset.nome));
  }
}

async function abrirImagem(nome) {
  if (estado.sujo && !confirm("Descartar alterações não salvas neste quadro?")) return;
  estado.atual = nome;
  estado.selecionada = -1;
  estado.sujo = false;
  estado.zoom = 1;
  estado.centroZoom = { x: 0.5, y: 0.5 };
  $("#t-nome-atual").textContent = nome;
  $("#t-vazio").hidden = true;
  $("#t-editor").hidden = false;
  renderizarLista();

  const [rotulo] = await Promise.all([
    fetch(`/api/treino/rotulo?nome=${encodeURIComponent(nome)}`).then((r) => r.json()),
  ]);
  estado.caixas = rotulo.caixas;

  const img = new Image();
  img.onload = () => { estado.imagemEl = img; desenhar(); };
  img.src = `/api/treino/imagem?nome=${encodeURIComponent(nome)}`;
}

function irPara(delta) {
  const itens = listaFiltrada();
  const indice = itens.findIndex((i) => i.nome === estado.atual);
  const alvo = itens[indice + delta];
  if (alvo) abrirImagem(alvo.nome);
}

// ---------------------------------------------------------------------------
// canvas: desenho e edição de caixas
// ---------------------------------------------------------------------------
function dimensoesCanvas() {
  const canvas = $("#t-canvas");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, W, H };
}

function retanguloFonte() {
  const img = estado.imagemEl;
  const fracao = 1 / estado.zoom;
  const sw = img.naturalWidth * fracao, sh = img.naturalHeight * fracao;
  const sx = Math.min(Math.max(0, estado.centroZoom.x * img.naturalWidth - sw / 2), img.naturalWidth - sw);
  const sy = Math.min(Math.max(0, estado.centroZoom.y * img.naturalHeight - sh / 2), img.naturalHeight - sh);
  return { sx, sy, sw, sh };
}

// Converte entre coordenadas normalizadas do quadro inteiro (0-1, o que fica salvo) e pixels do
// canvas na região atualmente ampliada — todo o resto do editor (desenho, clique, arraste) passa por aqui.
function transformo() {
  const img = estado.imagemEl;
  const canvas = $("#t-canvas");
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const { sx, sy, sw, sh } = retanguloFonte();
  return {
    W, H,
    paraTela: (xn, yn) => [((xn * img.naturalWidth - sx) / sw) * W, ((yn * img.naturalHeight - sy) / sh) * H],
    paraNorm: (px, py) => [(sx + (px / W) * sw) / img.naturalWidth, (sy + (py / H) * sh) / img.naturalHeight],
  };
}

function desenhar() {
  if (!estado.imagemEl) return;
  const { ctx, W, H } = dimensoesCanvas();
  const { sx, sy, sw, sh } = retanguloFonte();
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(estado.imagemEl, sx, sy, sw, sh, 0, 0, W, H);
  const t = transformo();
  estado.caixas.forEach((c, i) => desenharCaixa(ctx, t, c, i === estado.selecionada));
  $("#t-zoom-info").textContent = estado.zoom > 1.02 ? `zoom ${estado.zoom.toFixed(1)}x` : "";
}

function desenharCaixa(ctx, t, c, selecionada) {
  const [x1, y1] = t.paraTela(c.x1, c.y1);
  const [x2, y2] = t.paraTela(c.x2, c.y2);
  const cor = cssVar(CORES[c.classe] || "--sutil");
  ctx.lineWidth = selecionada ? 3 : 1.8;
  ctx.strokeStyle = cor;
  ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  ctx.font = "600 11px system-ui, sans-serif";
  const tw = ctx.measureText(c.classe).width + 8;
  ctx.fillStyle = cor;
  ctx.fillRect(x1, y1 >= 15 ? y1 - 15 : y2, tw, 15);
  ctx.fillStyle = "#fff";
  ctx.fillText(c.classe, x1 + 4, (y1 >= 15 ? y1 - 15 : y2) + 11);
  if (selecionada) {
    ctx.fillStyle = cor;
    for (const [hx, hy] of [[x1, y1], [x2, y1], [x1, y2], [x2, y2]]) {
      ctx.fillRect(hx - 4, hy - 4, 8, 8);
    }
  }
}

function posicaoNormalizada(e) {
  const canvas = $("#t-canvas");
  const rect = canvas.getBoundingClientRect();
  const px = e.clientX - rect.left, py = e.clientY - rect.top;
  const [x, y] = transformo().paraNorm(px, py);
  return { x: Math.min(Math.max(0, x), 1), y: Math.min(Math.max(0, y), 1), px, py };
}

function encontrarAlvo(px, py) {
  const t = transformo();
  for (let i = estado.caixas.length - 1; i >= 0; i--) {
    const c = estado.caixas[i];
    const [x1, y1] = t.paraTela(c.x1, c.y1);
    const [x2, y2] = t.paraTela(c.x2, c.y2);
    for (const [canto, hx, hy] of [["nw", x1, y1], ["ne", x2, y1], ["sw", x1, y2], ["se", x2, y2]]) {
      if (Math.hypot(px - hx, py - hy) <= 9) return { indice: i, modo: "redimensionar", canto };
    }
    if (px >= x1 && px <= x2 && py >= y1 && py <= y2) return { indice: i, modo: "mover" };
  }
  return null;
}

function marcarSujo() { estado.sujo = true; }

function configurarCanvas() {
  const canvas = $("#t-canvas");
  canvas.addEventListener("mousedown", (e) => {
    if (!estado.imagemEl) return;
    const { x, y, px, py } = posicaoNormalizada(e);
    const alvo = encontrarAlvo(px, py);
    if (alvo) {
      estado.selecionada = alvo.indice;
      estado.arrasto = { ...alvo, inicioX: x, inicioY: y, origem: { ...estado.caixas[alvo.indice] } };
    } else {
      const nova = { classe: estado.classeAtual, x1: x, y1: y, x2: x, y2: y };
      estado.caixas.push(nova);
      estado.selecionada = estado.caixas.length - 1;
      estado.arrasto = { indice: estado.selecionada, modo: "redimensionar", canto: "se", criando: true };
    }
    marcarSujo();
    desenhar();
  });

  window.addEventListener("mousemove", (e) => {
    if (!estado.arrasto) return;
    const { x, y } = posicaoNormalizada(e);
    const c = estado.caixas[estado.arrasto.indice];
    if (!c) return;
    if (estado.arrasto.modo === "mover") {
      const dx = x - estado.arrasto.inicioX, dy = y - estado.arrasto.inicioY;
      const o = estado.arrasto.origem;
      const w = o.x2 - o.x1, h = o.y2 - o.y1;
      c.x1 = Math.min(Math.max(0, o.x1 + dx), 1 - w);
      c.y1 = Math.min(Math.max(0, o.y1 + dy), 1 - h);
      c.x2 = c.x1 + w;
      c.y2 = c.y1 + h;
    } else {
      const nx = x, ny = y;
      if (estado.arrasto.canto === "nw") { c.x1 = Math.min(nx, c.x2 - MIN_CAIXA); c.y1 = Math.min(ny, c.y2 - MIN_CAIXA); }
      if (estado.arrasto.canto === "ne") { c.x2 = Math.max(nx, c.x1 + MIN_CAIXA); c.y1 = Math.min(ny, c.y2 - MIN_CAIXA); }
      if (estado.arrasto.canto === "sw") { c.x1 = Math.min(nx, c.x2 - MIN_CAIXA); c.y2 = Math.max(ny, c.y1 + MIN_CAIXA); }
      if (estado.arrasto.canto === "se") { c.x2 = Math.max(nx, c.x1 + MIN_CAIXA); c.y2 = Math.max(ny, c.y1 + MIN_CAIXA); }
    }
    desenhar();
  });

  window.addEventListener("mouseup", () => {
    if (!estado.arrasto) return;
    if (estado.arrasto.criando) {
      const c = estado.caixas[estado.arrasto.indice];
      if (c.x2 - c.x1 < MIN_CAIXA * 1.5 && c.y2 - c.y1 < MIN_CAIXA * 1.5) {
        // clique sem arrastar: cria uma caixa de tamanho padrão centrada no clique
        const cx = c.x1, cy = c.y1;
        c.x1 = Math.max(0, cx - 0.025); c.x2 = Math.min(1, cx + 0.025);
        c.y1 = Math.max(0, cy - 0.04); c.y2 = Math.min(1, cy + 0.04);
      }
    }
    estado.arrasto = null;
    desenhar();
  });

  window.addEventListener("keydown", (e) => {
    if (estado.selecionada < 0 || $("#t-editor").hidden) return;
    if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      estado.caixas.splice(estado.selecionada, 1);
      estado.selecionada = -1;
      marcarSujo();
      desenhar();
    } else if (["1", "2", "3"].includes(e.key)) {
      const classe = ["pinto", "galinha", "galo"][Number(e.key) - 1];
      estado.caixas[estado.selecionada].classe = classe;
      marcarSujo();
      desenhar();
    }
  });

  canvas.addEventListener("wheel", (e) => {
    if (!estado.imagemEl) return;
    e.preventDefault();
    const { x, y } = posicaoNormalizada(e);
    estado.centroZoom = { x, y };
    estado.zoom = Math.min(8, Math.max(1, estado.zoom * (e.deltaY < 0 ? 1.25 : 0.8)));
    desenhar();
  }, { passive: false });

  canvas.addEventListener("dblclick", () => {
    estado.zoom = 1;
    estado.centroZoom = { x: 0.5, y: 0.5 };
    desenhar();
  });

  new ResizeObserver(() => desenhar()).observe($("#t-canvas"));
}

function configurarChipsClasse() {
  const chips = $$(".chip-classe");
  const marcar = () => chips.forEach((c) => c.classList.toggle("ativo", c.dataset.classe === estado.classeAtual));
  chips.forEach((chip) => chip.addEventListener("click", () => {
    estado.classeAtual = chip.dataset.classe;
    if (estado.selecionada >= 0) { estado.caixas[estado.selecionada].classe = estado.classeAtual; marcarSujo(); desenhar(); }
    marcar();
  }));
  marcar();
}

// ---------------------------------------------------------------------------
// ações: salvar, sem-aves, navegação, extração, treino
// ---------------------------------------------------------------------------
async function salvarAtual() {
  if (!estado.atual) return;
  await fetch("/api/treino/rotulo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nome: estado.atual, caixas: estado.caixas }),
  });
  estado.sujo = false;
  const item = estado.imagens.find((i) => i.nome === estado.atual);
  if (item) { item.revisado = true; item.caixas = estado.caixas.length; }
  renderizarLista();
}

function configurarBotoesEditor() {
  $("#t-btn-salvar").addEventListener("click", async () => { await salvarAtual(); irPara(1); });
  $("#t-btn-sem-aves").addEventListener("click", async () => { estado.caixas = []; estado.selecionada = -1; desenhar(); await salvarAtual(); irPara(1); });
  $("#t-btn-anterior").addEventListener("click", async () => { if (estado.sujo) await salvarAtual(); irPara(-1); });
  $("#t-btn-proxima").addEventListener("click", async () => { if (estado.sujo) await salvarAtual(); irPara(1); });
  $("#t-filtro").addEventListener("change", renderizarLista);
  $("#t-ordenar").addEventListener("change", renderizarLista);
}

async function extrairQuadros() {
  const videos = $$("#t-videos input:checked").map((i) => i.value);
  if (!videos.length) return;
  const botao = $("#t-btn-extrair");
  const status = $("#t-extrair-status");
  botao.disabled = true;
  status.textContent = "Extraindo quadros e pré-rotulando com o modelo atual… pode levar um minuto.";
  try {
    const r = await fetch("/api/treino/extrair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        videos,
        intervalo_s: Number($("#t-intervalo").value),
        limite_por_video: Number($("#t-limite").value),
      }),
    });
    const corpo = await r.json();
    if (!corpo.ok) { status.textContent = corpo.erro || "Falha ao extrair."; return; }
    status.textContent = `${corpo.novos} quadros novos, ${corpo.rotulados} pré-rotulados. Revise na lista à esquerda.`;
    await carregarResumo();
  } catch {
    status.textContent = "Não foi possível falar com o servidor.";
  } finally {
    botao.disabled = false;
  }
}

// ---- treino ----------------------------------------------------------------
let sondaTreino = null;

function atualizarStatusTreino(job) {
  const status = $("#tr-status");
  const metricas = $("#tr-metricas");
  const botao = $("#tr-btn-iniciar");
  if (!job) return;
  botao.disabled = job.rodando;
  if (job.rodando) {
    status.textContent = `Treinando… ${job.mensagem || ""}`;
    metricas.hidden = true;
  } else if (job.erro) {
    status.textContent = `Falhou: ${job.erro}`;
  } else if (job.concluido) {
    status.textContent = `Concluído: ${job.concluido.pesos}`;
    renderizarMetricas(job.concluido);
  } else {
    status.textContent = "";
  }
}

function renderizarMetricas(concluido) {
  const alvo = $("#tr-metricas");
  const linhas = Object.entries(concluido.metricas)
    .map(([c, m]) => `<tr><td>${c}</td><td>${(m.precisao * 100).toFixed(0)}%</td><td>${(m.revocacao * 100).toFixed(0)}%</td></tr>`)
    .join("");
  alvo.innerHTML = `
    <table>
      <thead><tr><th>Classe</th><th>Precisão</th><th>Revocação</th></tr></thead>
      <tbody>${linhas}</tbody>
    </table>
    <button id="tr-btn-usar" class="botao primario pequeno">Usar este modelo</button>
  `;
  alvo.hidden = false;
  $("#tr-btn-usar").addEventListener("click", async () => {
    if (!confirm("Isso substitui modelos/pinteiro.pt (o atual vai para modelos/historico/). Continuar?")) return;
    await fetch("/api/treino/promover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pesos: concluido.pesos }),
    });
    $("#tr-status").textContent = "Modelo em uso atualizado.";
  });
}

function pararSondaTreino() {
  if (sondaTreino) clearInterval(sondaTreino);
  sondaTreino = null;
}

async function iniciarTreino() {
  const status = $("#tr-status");
  $("#tr-metricas").hidden = true;
  try {
    const r = await fetch("/api/treino/treinar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        epocas: Number($("#tr-epocas").value),
        imgsz: Number($("#tr-imgsz").value),
        batch: Number($("#tr-batch").value),
      }),
    });
    const corpo = await r.json();
    if (!corpo.ok) { status.textContent = corpo.erro || "Não foi possível iniciar o treino."; return; }
    status.textContent = `Treino iniciado: ${corpo.treino} imagens de treino, ${corpo.validacao} de validação.`;
    pararSondaTreino();
    sondaTreino = setInterval(async () => {
      const job = await fetch("/api/treino/status").then((r2) => r2.json());
      atualizarStatusTreino(job);
      if (!job.rodando) pararSondaTreino();
    }, 3000);
  } catch {
    status.textContent = "Não foi possível falar com o servidor.";
  }
}

// ---------------------------------------------------------------------------
$("#t-btn-extrair").addEventListener("click", extrairQuadros);
$("#tr-btn-iniciar").addEventListener("click", iniciarTreino);
configurarCanvas();
configurarChipsClasse();
configurarBotoesEditor();
carregarResumo();
