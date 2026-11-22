const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const errs = [];
  const ctx = await b.newContext({ viewport: { width: 390, height: 800 } }); const p = await ctx.newPage();
  p.on('pageerror', e => errs.push('pageerror ' + e.message)); p.on('console', m => m.type()==='error' && errs.push('console ' + m.text()));
  p.on('dialog', d => d.dismiss());
  await p.goto('http://localhost:8766/');
  const tab = n => p.click(`#nav button:text-is("${n}")`);
  await tab('Account'); await p.fill('#em','smoke@example.org'); await p.fill('#pw','Smokepass123'); await p.click('text=Register as applicant'); await p.waitForSelector('text=Export my data');
  await tab('Profile'); await p.fill('[data-k="full_name"]','Smoke Tester'); await p.fill('[data-k="nationality"]','LR'); await p.fill('[data-k="education.level"]','bachelor'); await p.click('text=Save profile'); await p.waitForSelector('text=readiness');
  for (const n of ['Countries','Opportunities','Matches','Applications','Advisor','Alerts','Safety','Staff']) { await tab(n); await p.waitForTimeout(400); }
  await tab('Safety'); await p.fill('#ft','We guarantee a visa, send a deposit to my personal account'); await p.click('text=Check for warning signs'); await p.waitForSelector('text=guarantee');
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  await tab('Account'); await p.click('text=Log out'); await p.waitForTimeout(800);
  await p.waitForSelector('#em'); await p.waitForTimeout(500); await p.fill('#em','root@example.org'); await p.fill('#pw','RootPassw0rdX'); await p.click('text=Log in'); await p.waitForSelector('text=super_admin');
  await tab('Staff'); await p.waitForSelector('text=Applicants'); await p.waitForSelector('text=Research sources');
  await p.waitForSelector('text=AI agents'); await p.click('button:text-is("Run now") >> nth=0'); await p.waitForTimeout(800); await p.click('button:text-is("Open") >> nth=0'); await p.waitForTimeout(1500); console.log((await p.innerText('#cd')).slice(0,300));
  await p.screenshot({ path: process.argv[2] + '/staff.png' });
  console.log(JSON.stringify({ errs, overflowAtPhoneWidth: overflow })); await b.close();
})().catch(e => { console.log('FAIL', e.message); process.exit(1); });
