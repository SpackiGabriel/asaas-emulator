// The same flow from Node.js 18+, using only the built-in fetch.
//
//   node examples/node/payout.mjs
//
// Point ASAAS_BASE_URL at the emulator (default http://localhost:8000).

const BASE_URL = process.env.ASAAS_BASE_URL ?? "http://localhost:8000";
const headers = { "access_token": "$aact_node_example", "Content-Type": "application/json" };

async function asaas(method, path, body) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(data)}`);
  return data;
}

const customer = await asaas("POST", "/v3/customers", {
  name: "Maria Souza",
  cpfCnpj: "52998224725",
});

const payment = await asaas("POST", "/v3/payments", {
  customer: customer.id,
  billingType: "PIX",
  value: 101.99,
  dueDate: "2030-01-10",
});

const pix = await asaas("GET", `/v3/payments/${payment.id}/pixQrCode`);
console.log("Pix copia e cola:", pix.payload.slice(0, 40) + "...");

await asaas("POST", `/v3/sandbox/payment/${payment.id}/confirm`);
console.log("Balance after payment:", (await asaas("GET", "/v3/finance/balance")).balance);

const transfer = await asaas("POST", "/v3/transfers", {
  value: 60,
  pixAddressKey: "owner@example.com",
  pixAddressKeyType: "EMAIL",
});
console.log("Payout:", transfer.status, transfer.endToEndIdentifier);
console.log("Balance after payout:", (await asaas("GET", "/v3/finance/balance")).balance);
