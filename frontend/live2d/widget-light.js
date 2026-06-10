// Live2D 看板娘 v7 — 修复 resize bug
(function() {
  var canvas = document.getElementById('live2d-canvas');
  if (!canvas) return;
  if (!PIXI || !PIXI.live2d || !PIXI.live2d.Live2DModel) return;

  var W = Math.round(window.innerWidth * 0.33);
  var H = Math.round(window.innerHeight * 0.70);
  var origW, origH; // 模型原始尺寸，永远不变

  var app = new PIXI.Application({
    view: canvas, width: W, height: H,
    backgroundAlpha: 0, antialias: true,
    resolution: window.devicePixelRatio || 1, autoDensity: true,
  });

  PIXI.live2d.Live2DModel.from('/static/live2d/%E5%A5%B8%E5%95%86.model3.json', {
    autoFocus: false, autoHitTest: false,
  }).then(function(model) {
    origW = model.width;
    origH = model.height;

    function fitModel() {
      var s = Math.min(W / origW, H / origH) * 0.92;
      model.scale.set(s);
      model.x = W * 0.5;
      model.y = H * 0.5;
      model.anchor.set(0.5, 0.5);
    }
    fitModel();
    app.stage.addChild(model);

    var core = model.internalModel.coreModel;
    if (!core || !core.setParameterValueById) return;

    var tx = 0, ty = 0;
    var eyeX = 0, eyeY = 0, headX = 0, headY = 0, headZ = 0;
    var bodyX = 0, bodyY = 0, bodyZ = 0;
    var hairF = 0, hairS = 0, hairB = 0;
    var armR = 0, armL = 0;
    var blinkState = 0, blinkTimer = 3, eyeO = 1;

    function clamp(v, min, max) { return v < min ? min : v > max ? max : v; }
    function updateTarget(e) {
      var rect = canvas.getBoundingClientRect();
      var dx = e.clientX - (rect.left + W * 0.5);
      var dy = e.clientY - (rect.top + H * 0.4);
      tx = clamp(dx / 400, -1, 1);
      ty = clamp(-dy / 300, -1, 1);
    }
    window.addEventListener('mousemove', updateTarget, { passive: true });
    document.addEventListener('mousemove', updateTarget, { passive: true });
    document.addEventListener('touchmove', function(e) {
      updateTarget({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY });
    }, { passive: true });

    function lerp(a, b, t) { return a + (b - a) * t; }

    app.ticker.add(function(delta) {
      var dt = Math.min(delta / 60, 0.1);
      try {
        eyeX = lerp(eyeX, tx, 0.1); eyeY = lerp(eyeY, ty, 0.1);
        core.setParameterValueById('ParamEyeBallX', eyeX);
        core.setParameterValueById('ParamEyeBallY', eyeY);

        headX = lerp(headX, tx * 10, 0.04); headY = lerp(headY, ty * 7, 0.04); headZ = lerp(headZ, tx * 4, 0.03);
        core.setParameterValueById('ParamAngleX', headX);
        core.setParameterValueById('ParamAngleY', headY);
        core.setParameterValueById('ParamAngleZ', headZ);

        bodyX = lerp(bodyX, tx * 4, 0.015); bodyY = lerp(bodyY, ty * 2, 0.015); bodyZ = lerp(bodyZ, -tx * 2, 0.01);
        core.setParameterValueById('ParamBodyAngleX', bodyX);
        core.setParameterValueById('ParamBodyAngleY', bodyY);
        core.setParameterValueById('ParamBodyAngleZ', bodyZ);

        var t = Date.now() * 0.001;
        hairF = lerp(hairF, Math.sin(t) * 0.8, 0.03);
        hairS = lerp(hairS, Math.sin(t * 1.3 + 0.5) * 0.7, 0.03);
        hairB = lerp(hairB, Math.sin(t * 1.1 + 1) * 0.5, 0.03);
        core.setParameterValueById('ParamHairFront', hairF);
        core.setParameterValueById('ParamHairSide', hairS);
        core.setParameterValueById('ParamHairBack', hairB);

        armR = lerp(armR, Math.sin(Date.now() * 0.0013) * 8 + 5, 0.03);
        armL = lerp(armL, Math.sin(Date.now() * 0.0013 + 1.5) * 8 - 5, 0.03);
        core.setParameterValueById('rightarmrotate', armR);
        core.setParameterValueById('leftarmrotate', armL);

        core.setParameterValueById('ParamBreath', 0.5 + Math.sin(Date.now() * 0.001) * 0.08);

        if (blinkState === 0) { blinkTimer -= dt; if (blinkTimer <= 0) blinkState = 1; }
        else if (blinkState === 1) { eyeO = Math.max(0, eyeO - 10 * dt); if (eyeO <= 0) { blinkState = 2; blinkTimer = 0.05; } }
        else if (blinkState === 2) { blinkTimer -= dt; if (blinkTimer <= 0) blinkState = 3; }
        else { eyeO = Math.min(1, eyeO + 8 * dt); if (eyeO >= 1) { blinkState = 0; blinkTimer = 2 + Math.random() * 4; } }
        core.setParameterValueById('ParamEyeLOpen', eyeO);
        core.setParameterValueById('ParamEyeROpen', eyeO);
      } catch(e) {}
    });

    // ── 气泡 ──
    var bubble = document.getElementById('live2d-bubble');
    var marketIsBull = true, bubbleTimer = null;
    var bullMsgs = ['走过路过不要错过~今天行情真不错呀！','温馨小提示：这些可都是值得入手的好票~','已经打过折了！你看这股价，多实惠！','嘿嘿，我这里应有尽有，包括牛股~','别多问，问就是全仓！','你看这个K线，像不像即将起飞的火箭？','本店严选，技术面完美，冲！','机不可失~你不买的话，我可就自己买了！','偷偷告诉你，我也偷偷加仓了~','人生最遥远的距离，就是牛股在面前却不敢买~','今日特惠组合：满仓+龙头，来一份？','根据我的计算，现在入场稳赚不赔！'];
    var bearMsgs = ['理财有风险，投资需谨慎呀…','咳咳，今天这个盘面…要不先喝杯茶？','虽然我很想推销，但良心偶尔也会痛一下的~','老板说了，不许趁大跌忽悠客人接盘…','我掐指一算，今天适合空仓摸鱼！','这个嘛…要不先去干点别的冷静一下？','打折是打折了，万一明天打骨折呢？','嘘…假装今天没开店，我先溜了~','哎呀这行情，看得我想躲回柜台下面…','管住手！这比管住钱包还重要！','跌了也别慌~反正钱也不是一天亏完的…','身为首席推荐官，我的专业意见：今天适合看戏~'];

    function syncMarket() {
      fetch('/api/market/status').then(function(r){return r.json()}).then(function(d){
        if(d&&d.above_ma20!==undefined)marketIsBull=!!d.above_ma20;
      }).catch(function(){});
    }
    syncMarket(); setInterval(syncMarket, 60000);

    function showBubble() {
      var msgs = marketIsBull ? bullMsgs : bearMsgs;
      if(bubble){
        var rect = canvas.getBoundingClientRect();
        bubble.style.left = (rect.left + W * 0.5) + 'px';
        bubble.style.top = (rect.top + H * -0.02) + 'px';
        bubble.textContent = msgs[Math.floor(Math.random()*msgs.length)];
        bubble.classList.add('show');
      }
      if(bubbleTimer)clearTimeout(bubbleTimer);
      bubbleTimer = setTimeout(function(){if(bubble)bubble.classList.remove('show');},4000);
    }

    canvas.addEventListener('click', function(e){e.stopPropagation();syncMarket();showBubble();});
    canvas.addEventListener('touchend', function(e){e.preventDefault();syncMarket();showBubble();});

    window.addEventListener('resize', function() {
      W = Math.round(window.innerWidth * 0.33);
      H = Math.round(window.innerHeight * 0.70);
      app.renderer.resize(W, H);
      fitModel(); // 使用保存的 origW/origH
    });
  });
})();
