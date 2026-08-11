import { expect, test } from "@playwright/test";

const apiUrl = process.env.E2E_API_URL ?? "http://127.0.0.1:2024";

function appUrl() {
  return `/?apiUrl=${encodeURIComponent(apiUrl)}`;
}

test("rejecting a proposed resolution leaves the case unresolved", async ({
  page,
}) => {
  await page.goto(appUrl());
  await page.getByRole("button", { name: "CASE-1001" }).click();
  await page
    .getByPlaceholder("输入案件编号，例如：分析CASE-1001")
    .fill("分析CASE-1001并建议处理方式");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("等待人工确认")).toBeVisible();
  await expect(page.getByText("10分钟内有效")).toBeVisible();
  await expect(page.getByText("confirmation_token")).toHaveCount(0);
  await expect(page.getByText("run_id")).toHaveCount(0);

  await page.getByRole("button", { name: "拒绝执行" }).click();

  await expect(page.getByText("已取消，本次未修改案件")).toBeVisible();
  await expect(page.getByText("案件状态：待处理")).toBeVisible();
});

test("approving a proposed resolution shows its audit reference", async ({
  page,
}) => {
  await page.goto(appUrl());
  await page.getByRole("button", { name: "CASE-1002" }).click();
  await page
    .getByPlaceholder("输入案件编号，例如：分析CASE-1001")
    .fill("分析CASE-1002并建议处理方式");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("等待人工确认")).toBeVisible();
  await page.getByRole("button", { name: "确认执行" }).click();

  await expect(page.getByText("案件已解决")).toBeVisible();
  await expect(page.getByText(/^审计事件编号：\d+$/)).toBeVisible();
});
