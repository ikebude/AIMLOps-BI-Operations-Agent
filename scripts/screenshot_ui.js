// Usage: NODE_PATH=$(npm root -g) node scripts/screenshot_ui.js [url] [out.png]
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8599';
  const out = process.argv[3] || 'img/ui_streamlit.png';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(url);
  await page.waitForSelector('h1');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: out });
  await browser.close();
})();
