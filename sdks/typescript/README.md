# @rugmunch/sdk@0.1.0

A TypeScript SDK client for the localhost API.

## Usage

First, install the SDK from npm.

```bash
npm install @rugmunch/sdk --save
```

Next, try it out.


```ts
import {
  Configuration,
  AdminAlertsApi,
} from '@rugmunch/sdk';
import type { AlertsCriticalWebhookApiV1AdminAlertsCriticalPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AdminAlertsApi();

  const body = {
    // AlertmanagerPayload
    alertmanagerPayload: ...,
  } satisfies AlertsCriticalWebhookApiV1AdminAlertsCriticalPostRequest;

  try {
    const data = await api.alertsCriticalWebhookApiV1AdminAlertsCriticalPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```


## Documentation

### API Endpoints

All URIs are relative to *http://localhost*

| Class | Method | HTTP request | Description
| ----- | ------ | ------------ | -------------
*AdminAlertsApi* | [**alertsCriticalWebhookApiV1AdminAlertsCriticalPost**](docs/AdminAlertsApi.md#alertscriticalwebhookapiv1adminalertscriticalpost) | **POST** /api/v1/admin/alerts/critical | Alerts Critical Webhook
*AdminAlertsApi* | [**alertsRecentApiV1AdminAlertsRecentGet**](docs/AdminAlertsApi.md#alertsrecentapiv1adminalertsrecentget) | **GET** /api/v1/admin/alerts/recent | Alerts Recent
*AdminAlertsApi* | [**alertsWebhookApiV1AdminAlertsWebhookPost**](docs/AdminAlertsApi.md#alertswebhookapiv1adminalertswebhookpost) | **POST** /api/v1/admin/alerts/webhook | Alerts Webhook
*AlertsApi* | [**cancelApiV1AlertsAlertIdDelete**](docs/AlertsApi.md#cancelapiv1alertsalertiddelete) | **DELETE** /api/v1/alerts/{alert_id} | Cancel
*AlertsApi* | [**listAlertsApiV1AlertsGet**](docs/AlertsApi.md#listalertsapiv1alertsget) | **GET** /api/v1/alerts | List Alerts
*AlertsApi* | [**subscribeApiV1AlertsSubscribePost**](docs/AlertsApi.md#subscribeapiv1alertssubscribepost) | **POST** /api/v1/alerts/subscribe | Subscribe
*CatalogApi* | [**attachRagApiV1CatalogTokensChainAddressAttachRagPost**](docs/CatalogApi.md#attachragapiv1catalogtokenschainaddressattachragpost) | **POST** /api/v1/catalog/tokens/{chain}/{address}/attach-rag | Attach Rag
*CatalogApi* | [**getTokenApiV1CatalogTokensChainAddressGet**](docs/CatalogApi.md#gettokenapiv1catalogtokenschainaddressget) | **GET** /api/v1/catalog/tokens/{chain}/{address} | Get Token
*CatalogApi* | [**getTokenRiskApiV1CatalogTokensChainAddressRiskGet**](docs/CatalogApi.md#gettokenriskapiv1catalogtokenschainaddressriskget) | **GET** /api/v1/catalog/tokens/{chain}/{address}/risk | Get Token Risk
*CatalogApi* | [**getWalletApiV1CatalogWalletsChainAddressGet**](docs/CatalogApi.md#getwalletapiv1catalogwalletschainaddressget) | **GET** /api/v1/catalog/wallets/{chain}/{address} | Get Wallet
*CatalogApi* | [**probeApiV1CatalogProbeGet**](docs/CatalogApi.md#probeapiv1catalogprobeget) | **GET** /api/v1/catalog/probe | Probe
*CatalogApi* | [**ragIngestApiV1CatalogRagIngestPost**](docs/CatalogApi.md#ragingestapiv1catalogragingestpost) | **POST** /api/v1/catalog/rag/ingest | Rag Ingest
*CatalogApi* | [**ragSearchApiV1CatalogRagSearchPost**](docs/CatalogApi.md#ragsearchapiv1catalogragsearchpost) | **POST** /api/v1/catalog/rag/search | Rag Search
*CatalogApi* | [**resolveEntityApiV1CatalogEntitiesResolvePost**](docs/CatalogApi.md#resolveentityapiv1catalogentitiesresolvepost) | **POST** /api/v1/catalog/entities/resolve | Resolve Entity
*CatalogApi* | [**riskyTokensApiV1CatalogTokensRiskyByDeployerPost**](docs/CatalogApi.md#riskytokensapiv1catalogtokensriskybydeployerpost) | **POST** /api/v1/catalog/tokens/risky-by-deployer | Risky Tokens
*CatalogApi* | [**statsApiV1CatalogStatsGet**](docs/CatalogApi.md#statsapiv1catalogstatsget) | **GET** /api/v1/catalog/stats | Stats
*HealthApi* | [**healthHealthGet**](docs/HealthApi.md#healthhealthget) | **GET** /health | Health
*HealthApi* | [**liveLiveGet**](docs/HealthApi.md#liveliveget) | **GET** /live | Live
*HealthApi* | [**readyReadyGet**](docs/HealthApi.md#readyreadyget) | **GET** /ready | Ready
*McpApi* | [**directCallMcpCallToolIdPost**](docs/McpApi.md#directcallmcpcalltoolidpost) | **POST** /mcp/call/{tool_id} | Direct Call
*McpApi* | [**jsonrpcHandlerMcpPost**](docs/McpApi.md#jsonrpchandlermcppost) | **POST** /mcp | Jsonrpc Handler
*McpApi* | [**listToolsMcpToolsGet**](docs/McpApi.md#listtoolsmcptoolsget) | **GET** /mcp/tools | List Tools
*MetaApi* | [**versionVersionGet**](docs/MetaApi.md#versionversionget) | **GET** /version | Version
*NewsApi* | [**analyzeNewsApiV1NewsNewsIdAnalyzePost**](docs/NewsApi.md#analyzenewsapiv1newsnewsidanalyzepost) | **POST** /api/v1/news/{news_id}/analyze | Analyze News
*NewsApi* | [**getNewsApiV1NewsNewsIdGet**](docs/NewsApi.md#getnewsapiv1newsnewsidget) | **GET** /api/v1/news/{news_id} | Get News
*NewsApi* | [**listNewsApiV1NewsPost**](docs/NewsApi.md#listnewsapiv1newspost) | **POST** /api/v1/news | List News
*NewsApi* | [**trendingNewsApiV1NewsTrendingGet**](docs/NewsApi.md#trendingnewsapiv1newstrendingget) | **GET** /api/v1/news/trending | Trending News
*NewsAdminApi* | [**triggerIngestApiV1NewsAdminIngestPost**](docs/NewsAdminApi.md#triggeringestapiv1newsadminingestpost) | **POST** /api/v1/news/_admin/ingest | Trigger Ingest
*RagApi* | [**bulkApiV1RagV2BulkIngestPost**](docs/RagApi.md#bulkapiv1ragv2bulkingestpost) | **POST** /api/v1/rag/v2/bulk-ingest | Bulk
*RagApi* | [**feedbackApiV1RagV2FeedbackPost**](docs/RagApi.md#feedbackapiv1ragv2feedbackpost) | **POST** /api/v1/rag/v2/feedback | Feedback
*RagApi* | [**ingestApiV1RagV2IngestPost**](docs/RagApi.md#ingestapiv1ragv2ingestpost) | **POST** /api/v1/rag/v2/ingest | Ingest
*RagApi* | [**searchApiV1RagV2SearchPost**](docs/RagApi.md#searchapiv1ragv2searchpost) | **POST** /api/v1/rag/v2/search | Search
*RagApi* | [**statsApiV1RagV2StatsGet**](docs/RagApi.md#statsapiv1ragv2statsget) | **GET** /api/v1/rag/v2/stats | Stats
*ReportsApi* | [**generateReportApiV1ReportsGeneratePost**](docs/ReportsApi.md#generatereportapiv1reportsgeneratepost) | **POST** /api/v1/reports/generate | Generate Report
*ReportsApi* | [**getReportApiV1ReportsReportIdGet**](docs/ReportsApi.md#getreportapiv1reportsreportidget) | **GET** /api/v1/reports/{report_id} | Get Report
*ScannerApi* | [**scanApiV1ScannerScanPost**](docs/ScannerApi.md#scanapiv1scannerscanpost) | **POST** /api/v1/scanner/scan | Scan
*TokenApi* | [**getDetailApiV1TokenAddressGet**](docs/TokenApi.md#getdetailapiv1tokenaddressget) | **GET** /api/v1/token/{address} | Get Detail
*TokenApi* | [**getHoldersApiV1TokenAddressHoldersGet**](docs/TokenApi.md#getholdersapiv1tokenaddressholdersget) | **GET** /api/v1/token/{address}/holders | Get Holders
*TokenApi* | [**getLiquidityApiV1TokenAddressLiquidityGet**](docs/TokenApi.md#getliquidityapiv1tokenaddressliquidityget) | **GET** /api/v1/token/{address}/liquidity | Get Liquidity
*TokenApi* | [**getRiskApiV1TokenAddressRiskGet**](docs/TokenApi.md#getriskapiv1tokenaddressriskget) | **GET** /api/v1/token/{address}/risk | Get Risk
*TokenApi* | [**scanApiV1TokenScanPost**](docs/TokenApi.md#scanapiv1tokenscanpost) | **POST** /api/v1/token/scan | Scan
*WalletApi* | [**getAnalysisApiV1WalletAddressAnalysisGet**](docs/WalletApi.md#getanalysisapiv1walletaddressanalysisget) | **GET** /api/v1/wallet/{address}/analysis | Get Analysis
*WalletApi* | [**getBalanceApiV1WalletAddressBalanceGet**](docs/WalletApi.md#getbalanceapiv1walletaddressbalanceget) | **GET** /api/v1/wallet/{address}/balance | Get Balance
*WalletApi* | [**getTransactionsApiV1WalletAddressTransactionsGet**](docs/WalletApi.md#gettransactionsapiv1walletaddresstransactionsget) | **GET** /api/v1/wallet/{address}/transactions | Get Transactions
*WalletApi* | [**scanApiV1WalletScanPost**](docs/WalletApi.md#scanapiv1walletscanpost) | **POST** /api/v1/wallet/scan | Scan
*X402Api* | [**catalogApiV1X402CatalogGet**](docs/X402Api.md#catalogapiv1x402catalogget) | **GET** /api/v1/x402/catalog | Catalog
*X402Api* | [**receiptApiV1X402ReceiptsTxHashGet**](docs/X402Api.md#receiptapiv1x402receiptstxhashget) | **GET** /api/v1/x402/receipts/{tx_hash} | Receipt
*X402Api* | [**usageApiV1X402UsageGet**](docs/X402Api.md#usageapiv1x402usageget) | **GET** /api/v1/x402/usage | Usage
*X402Api* | [**verifyPaymentApiV1X402VerifyPost**](docs/X402Api.md#verifypaymentapiv1x402verifypost) | **POST** /api/v1/x402/verify | Verify Payment


### Models

- [AlertSubscription](docs/AlertSubscription.md)
- [AlertType](docs/AlertType.md)
- [AlertmanagerAlert](docs/AlertmanagerAlert.md)
- [AlertmanagerPayload](docs/AlertmanagerPayload.md)
- [AppDomainScannerModelsScanRequest](docs/AppDomainScannerModelsScanRequest.md)
- [AppDomainTokenModelsRiskLevel](docs/AppDomainTokenModelsRiskLevel.md)
- [AppDomainWalletModelsScanRequest](docs/AppDomainWalletModelsScanRequest.md)
- [AttachRagRequest](docs/AttachRagRequest.md)
- [Balance](docs/Balance.md)
- [BulkIngestRequest](docs/BulkIngestRequest.md)
- [CatalogResponse](docs/CatalogResponse.md)
- [CreateAlertRequest](docs/CreateAlertRequest.md)
- [FeedbackRecord](docs/FeedbackRecord.md)
- [FindRiskyTokensRequest](docs/FindRiskyTokensRequest.md)
- [GenerateRequest](docs/GenerateRequest.md)
- [GenerateResponse](docs/GenerateResponse.md)
- [HTTPValidationError](docs/HTTPValidationError.md)
- [Id](docs/Id.md)
- [IngestRequest](docs/IngestRequest.md)
- [IngestResult](docs/IngestResult.md)
- [JsonRpcRequest](docs/JsonRpcRequest.md)
- [LocationInner](docs/LocationInner.md)
- [NewsAnalysisResponse](docs/NewsAnalysisResponse.md)
- [NewsItemOut](docs/NewsItemOut.md)
- [NewsListResponse](docs/NewsListResponse.md)
- [PaginatedResponse](docs/PaginatedResponse.md)
- [RagIngestRequest](docs/RagIngestRequest.md)
- [RagSearchRequest](docs/RagSearchRequest.md)
- [ReceiptResponse](docs/ReceiptResponse.md)
- [ResolveEntityRequest](docs/ResolveEntityRequest.md)
- [ScanFlag](docs/ScanFlag.md)
- [ScanModuleResult](docs/ScanModuleResult.md)
- [ScanResponse](docs/ScanResponse.md)
- [ScanResult](docs/ScanResult.md)
- [ScanTier](docs/ScanTier.md)
- [SearchHit](docs/SearchHit.md)
- [SearchRequest](docs/SearchRequest.md)
- [SearchResponse](docs/SearchResponse.md)
- [TokenDetail](docs/TokenDetail.md)
- [TokenHolding](docs/TokenHolding.md)
- [TokenRisk](docs/TokenRisk.md)
- [TokenScanRequest](docs/TokenScanRequest.md)
- [TokenScanResult](docs/TokenScanResult.md)
- [Transaction](docs/Transaction.md)
- [UsageResponse](docs/UsageResponse.md)
- [ValidationError](docs/ValidationError.md)
- [WalletAnalysis](docs/WalletAnalysis.md)

### Authorization

Endpoints do not require authorization.


## About

This TypeScript SDK client supports the [Fetch API](https://fetch.spec.whatwg.org/)
and is automatically generated by the
[OpenAPI Generator](https://openapi-generator.tech) project:

- API version: `2026.06.21`
- Package version: `0.1.0`
- Generator version: `7.23.0`
- Build package: `org.openapitools.codegen.languages.TypeScriptFetchClientCodegen`

The generated npm module supports the following:

- Environments
  * Node.js
  * Webpack
  * Browserify
- Language levels
  * ES5 - you must have a Promises/A+ library installed
  * ES6
- Module systems
  * CommonJS
  * ES6 module system


## Development

### Building

To build the TypeScript source code, you need to have Node.js and npm installed.
After cloning the repository, navigate to the project directory and run:

```bash
npm install
npm run build
```

### Publishing

Once you've built the package, you can publish it to npm:

```bash
npm publish
```

## License

[]()
