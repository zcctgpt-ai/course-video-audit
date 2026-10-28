// 取流 worker：优先复用已登录的 CDP 浏览器(127.0.0.1:9222)，否则独立启动 + 注入 cookie
// 用法: node fetch_stream.js <watch_url> [cookie_json路径]
// 输出: 单行 JSON { type, url, title, liveStart, ok, mode, loginWall?, error? }
// 铁律: 结尾必须 process.exit(0)（CDP 模式下绝不能 browser.close()，会挂死宿主）
const { chromium } = require('playwright-core');
const fs = require('fs');

const CHROME = process.env.AUDIT_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

(async () => {
  const pageUrl = process.argv[2];
  const cookieFile = process.argv[3] || '';
  const out = { type: null, url: null, title: '', liveStart: null, ok: false, mode: null };
  let browser = null, ctx, usingCDP = false;
  try {
    // 1. 尝试连接已登录的 CDP 浏览器
    try {
      browser = await chromium.connectOverCDP('http://127.0.0.1:9222', { timeout: 5000 });
      ctx = browser.contexts()[0];
      usingCDP = true;
    } catch (e) {
      // 2. 回退：独立浏览器 + 注入 cookie
      browser = await chromium.launch({
        executablePath: CHROME,
        headless: true,
      });
      ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 800 } });
      if (cookieFile && fs.existsSync(cookieFile)) {
        const cookies = JSON.parse(fs.readFileSync(cookieFile, 'utf-8'));
        if (cookies.length) await ctx.addCookies(cookies);
      }
    }
    out.mode = usingCDP ? 'cdp' : 'standalone';
    const page = await ctx.newPage();
    page.on('request', (r) => {
      const u = r.url();
      if (out.type === 'm3u8' || out.type === 'mp4') return;
      if (/\.m3u8/.test(u)) { out.type = 'm3u8'; out.url = u; }
      else if (/^https:\/\/dpv\.videocc\.net\/.+\.mp4/.test(u)) { out.type = 'mp4'; out.url = u.split('?')[0]; }
    });
    await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(6000);
    out.title = await page.title();
    if (/登录/.test(out.title) || /登录\/注册/.test(await page.evaluate(() => document.body.innerText.slice(0, 300)))) {
      out.loginWall = true;
    }
    try {
      const bodyText = await page.evaluate(() => document.body.innerText);
      const m = bodyText.match(/直播时间[：:]\s*([\d.]{8,10}\s+\d{1,2}[:：]\d{2})/);
      if (m) out.liveStart = m[1];
    } catch (e) {}
    // 多种按钮文案尝试（栏目落地页是"进入直播间"，专场页是"立即回看"）
    for (const label of ['进入直播间', '立即回看', '回看', '看回放', '播放回放']) {
      if (out.url) break;
      try { await page.getByText(label, { exact: false }).first().click({ timeout: 5000 }); } catch (e) {}
      for (let i = 0; i < 10 && !out.url; i++) await page.waitForTimeout(2000);
    }
    for (let i = 0; i < 10 && !out.url; i++) await page.waitForTimeout(2000);
    out.ok = !!out.url;
    try { await page.close(); } catch (e) {}
  } catch (e) {
    out.error = String(e.message).split('\n')[0];
  }
  console.log(JSON.stringify(out));
  process.exit(0); // CDP 模式下不 close browser，直接断开；绝不调用 browser.close()
})();
