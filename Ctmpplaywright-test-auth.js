const { chromium } = require('playwright');

const TARGET_URL = 'http://localhost:3000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const consoleErrors = [];
  const consoleLogs = [];

  page.on('console', msg => {
    const text = msg.text();
    if (msg.type() === 'error') {
      consoleErrors.push(text);
    }
    consoleLogs.push(`[${msg.type()}] ${text}`);
  });

  page.on('pageerror', err => {
    consoleErrors.push(err.message);
  });

  try {
    await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(2000);

    console.log('Page title:', await page.title());
    console.log('Current URL:', page.url());

    // Screenshot
    await page.screenshot({ path: 'C:\tmp\screenshot-home.png', fullPage: true });
    console.log('Screenshot saved');

    // Check login page
    await page.goto(`${TARGET_URL}/login`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: 'C:\tmp\screenshot-login.png', fullPage: true });
    console.log('Login screenshot saved');

    console.log('--- Console Logs ---');
    consoleLogs.forEach(l => console.log(l));
    console.log('--- Console Errors ---');
    if (consoleErrors.length === 0) {
      console.log('No console errors');
    } else {
      consoleErrors.forEach(e => console.log(e));
    }
  } catch (error) {
    console.error('Error:', error.message);
  } finally {
    await browser.close();
  }
})();
