# Referência Visual — Galinhas Adultas

Foto de galpão comercial usada como referência para treinar/validar o modelo
`pinteiro.pt` (YOLO11n, 3 classes: pinto / galinha / galo).

**Imagem**: galinheiro industrial com maravalha/cepilho no piso, sistema de
bebedouros nipple vermelhos à esquerda, ninhos metálicos com ovos marrons à
direita, ventiladores/exaustores ao fundo.

## Variedades visíveis na foto

| # | Cor / Padrão | Provável raça / linhagem | Características |
|---|---|---|---|
| 1 | **Branca** | Leghorn branca (poedeiras industriais) | Pena densa branca, crista e barbela vermelhas vivas, bico amarelo, patas amarelas, porte médio |
| 2 | **Marrom-avermelhada** | ISA Brown / Lohmann Brown / Rhode Island Red | Pena vermelha-acaju uniforme, cauda escura, crista grande, postura ereta, bico amarelo, patas amarelas |
| 3 | **Preta** | Australorp / Rhode Island Red preto | Pena preta densa com brilho esverdeado, crista média, bico e patas escuras |
| 4 | **Rajada preto-e-branco** | Plymouth Rock "carijó" (Barred Rock) | Penas com listras horizontais alternadas (carijó), crista serrilhada simples, bico e patas amarelas |
| 5 | **Cinza-azulada** | Andaluza azul / Plymouth Rock lavanda | Pena cinza com tom azulado, porte médio, crista vermelha |

## Ambiente do galpão (contexto pro modelo)

- **Piso**: maravalha (cepilho de madeira) — tons bege/marrom
- **Bebedouros**: nipples vermelhos com tubo horizontal, à esquerda
- **Ninhos**: caixas metálicas escuras em bateria, com ovos marrons visíveis
- **Ventilação**: exaustores circulares grandes ao fundo
- **Luz**: natural vinda de janelas laterais (lado esquerdo)

## Lição para o modelo

O `pinteiro.pt` deve ser **robusto a**:

1. **Variabilidade cromática**: galinhas vêm em 5+ cores básicas — não pode
   depender de cor específica.
2. **Densidade**: galpões comerciais têm dezenas/milhas de aves no mesmo
   frame — o modelo precisa lidar com alta sobreposição.
3. **Pose**: galinhas em pé, bicando, deitadas em ninho, no poleiro.
4. **Ângulo**: vistas lateral (mais comum), frontal (no ninho), e dorsal
   (costas quando se afasta).
5. **Oclusão parcial**: aves parcialmente cobertas por outras aves, pelo
   comedouro ou pelo ninho.

## Features visuais que distinguem galinha adulta de pinto

| Feature | Pinto (até ~6 semanas) | Galinha adulta |
|---|---|---|
| Tamanho relativo | Pequeno (1/3 a 1/2 da galinha) | Médio-grande |
| Crista | Ausente ou rudimentar | Bem desenvolvida (vermelha) |
| Barbela | Ausente | Presente, pendente |
| Pena | Penugem fofa amarela | Pena densa, colorida, definida |
| Cauda | Curta, sem formação | Formada com penas longas |
| Bico | Pequeno, amarelo-claro | Amarelo, mais robusto |
| Patas | Finas, rosadas/amareladas | Robustas, amarelas/escamas |

## Próximos passos (quando o usuário decidir treinar)

Para treinar com base nesta referência:

1. **Coletar dataset real**: mínimo 100-300 imagens anotadas (do próprio
   galpão do usuário ou dataset público — ex.: PIO Poultry Object Detection
   do Zenodo, 1487 imagens).
2. **Estratégia recomendada** (vs tentativa anterior que regrediu):
   - **lr=5e-5** (10× menor) pra preservar pré-treino
   - **Freeze backbone** (primeiras camadas do YOLO) — só treina a cabeça
   - **5 epochs** com early-stopping se mAP da classe `pinto` cair >5%
   - **Balanceamento**: peso 2× pra classe `pinto` no loss (menos amostras)
   - **Validação por classe**: monitorar mAP separado por classe, não
     só mAP global
3. **Avaliar antes de deploy**: comparar V1 vs V2 no vídeo real, contando
   detecções por classe. NÃO fazer deploy se V2 detecta menos pintos.
