// 临时自检配置（不下载浏览器：用本机已有的 msedge.exe）
const { defineConfig } = require(process.env.PW_TEST_MOD || 'playwright/test');
module.exports = defineConfig({
  timeout: 60000,
  use: {
    launchOptions: {
      executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    },
  },
});
