"use strict";

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
const $$ = (seletor, raiz = document) => [...raiz.querySelectorAll(seletor)];
const CORES = { pinto: "--classe-pinto", galinha: "--classe-galinha", galo: "--classe-galo" };

const app = { status: { rodando: false }, ultimo: null, tokenImagem: 0 };

function cssVar(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function duracao(segundos) {
  const s = Math.max(0, Math.floor(segundos ?? 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

function esc(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------
function conectar() {
  const fonte = new EventSource("/api/eventos");
  fonte.onopen = () => definirConexao(true);
  fonte.onerror = () => definirConexao(false);
  fonte.addEventListener("status", (e) => aplicarStatus(JSON.parse(e.data)));
  fonte.addEventListener("estado", (e) => receberEstado(JSON.parse(e.data)));
}

function definirConexao(ok) {
  const badge = $("#badge-conexao");
  badge.textContent = ok ? "Conectado" : "Sem conexão com o servidor";
  badge.classList.toggle("erro", !ok);
}

function aplicarStatus(st) {
  app.status = st;
  const badge = $("#badge-rodando");
  badge.textContent = st.rodando ? "Monitorando" : "Parado";
  badge.classList.toggle("ativo", st.rodando);
  if (!st.rodando) {
    app.ultimo = null;
    $("#tela-vazia").hidden = false;
  }
}

// ---------------------------------------------------------------------------
// KPIs e tabela de rastreio
// ---------------------------------------------------------------------------
function atualizarKpis(rastreio) {
  const semIdentidade = !rastreio;
  $("#r-sem-identidade").hidden = !app.status.rodando || !semIdentidade;
  if (semIdentidade) {
    for (const id of ["r-ativos", "r-esperado", "r-criados", "r-costuras", "r-taxa"]) $(`#${id}`).textContent = "–";
    $("#r-pico").textContent = "";
    return;
  }
  $("#r-ativos").textContent = rastreio.ids_ativos;
  $("#r-esperado").textContent = rastreio.populacao_esperada ?? "–";
  $("#r-criados").textContent = rastreio.total_ids_criados;
  $("#r-costuras").textContent = rastreio.costuras_totais;
  $("#kpi-costuras").classList.toggle("ativo", rastreio.costuras_totais > 0);

  const desde = app.status.desde ? new Date(app.status.desde) : null;
  const minutos = desde ? Math.max((Date.now() - desde.getTime()) / 60000, 1 / 60) : null;
  $("#r-taxa").textContent = minutos ? (rastreio.costuras_totais / minutos).toFixed(1) : "–";

  const picos = Object.entries(rastreio.pico_por_classe || {}).map(([c, n]) => `${c}: ${n}`);
  $("#r-pico").textContent = picos.length
    ? `Teto aprendido por classe (maior nº visto ao mesmo tempo — a partir dele, ID novo tenta religar numa ave sumida): ${picos.join(" · ")}`
    : "";
}

function renderizarTabela(rastreio) {
  const corpo = $("#tabela-rastreio tbody");
  if (!rastreio || !rastreio.aves.length) {
    corpo.innerHTML = `<tr><td colspan="5" class="vazio">Nenhuma ave rastreada ainda.</td></tr>`;
    return;
  }
  const aves = [...rastreio.aves].sort((a, b) => b.costuras - a.costuras || a.id - b.id);
  corpo.innerHTML = aves.map((a) => {
    const sumida = a.visto_ha_s != null && a.visto_ha_s > 3;
    const classes = [a.costuras >= 3 ? "instavel" : "", sumida ? "sumida" : ""].join(" ");
    return `<tr class="${classes}">
      <td>#${esc(a.id)}</td>
      <td>${esc(a.classe)}</td>
      <td>${duracao(a.criado_ha_s)}</td>
      <td>${a.visto_ha_s == null ? "–" : sumida ? `sumida há ${duracao(a.visto_ha_s)}` : "agora"}</td>
      <td>${a.costuras}</td>
    </tr>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// canvas: só caixa + #id + classe, sem cor de comportamento nem zonas
// ---------------------------------------------------------------------------
function receberEstado(estado) {
  atualizarKpis(estado.rastreio);
  renderizarTabela(estado.rastreio);

  if (estado.imagem) {
    const token = ++app.tokenImagem;
    const img = new Image();
    img.onload = () => { if (token === app.tokenImagem) desenhar(estado, img); };
    img.src = `data:image/jpeg;base64,${estado.imagem}`;
  } else {
    desenhar(estado, null);
  }
  $("#tela-vazia").hidden = true;
  let info = `frame ${estado.frame} · t ${estado.t}s · ${estado.fps} fps`;
  $("#r-info").textContent = info;
}

function desenhar(estado, imagem) {
  app.ultimo = { estado, imagem };
  const canvas = $("#canvas");
  const [largura, altura] = estado.dim;
  $("#tela").style.setProperty("--proporcao", (largura / altura).toFixed(4));

  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  if (imagem) ctx.drawImage(imagem, 0, 0, W, H);

  for (const d of estado.deteccoes) desenharCaixa(ctx, d, W, H);
}

function desenharCaixa(ctx, d, W, H) {
  const [x1, y1, x2, y2] = [d.caixa[0] * W, d.caixa[1] * H, d.caixa[2] * W, d.caixa[3] * H];
  const cor = cssVar(CORES[d.classe] || "--sutil");
  ctx.lineWidth = 1.8;
  ctx.strokeStyle = cor;
  ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

  const texto = `#${d.id ?? "?"} ${d.classe || ""}`;
  ctx.font = "600 11px system-ui, sans-serif";
  const tw = ctx.measureText(texto).width + 8;
  const ty = y1 >= 17 ? y1 - 16 : y2 + 1;
  ctx.fillStyle = cor;
  ctx.fillRect(x1, ty, tw, 15);
  ctx.fillStyle = "#fff";
  ctx.fillText(texto, x1 + 4, ty + 11);
}

// ---------------------------------------------------------------------------
conectar();
new ResizeObserver(() => app.ultimo && desenhar(app.ultimo.estado, app.ultimo.imagem)).observe($("#tela"));
