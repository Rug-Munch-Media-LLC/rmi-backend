# WalletApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getAnalysisApiV1WalletAddressAnalysisGet**](WalletApi.md#getanalysisapiv1walletaddressanalysisget) | **GET** /api/v1/wallet/{address}/analysis | Get Analysis |
| [**getBalanceApiV1WalletAddressBalanceGet**](WalletApi.md#getbalanceapiv1walletaddressbalanceget) | **GET** /api/v1/wallet/{address}/balance | Get Balance |
| [**getTransactionsApiV1WalletAddressTransactionsGet**](WalletApi.md#gettransactionsapiv1walletaddresstransactionsget) | **GET** /api/v1/wallet/{address}/transactions | Get Transactions |
| [**scanApiV1WalletScanPost**](WalletApi.md#scanapiv1walletscanpost) | **POST** /api/v1/wallet/scan | Scan |



## getAnalysisApiV1WalletAddressAnalysisGet

> WalletAnalysis getAnalysisApiV1WalletAddressAnalysisGet(address, chain)

Get Analysis

Full wallet analysis: risk score + tokens + recent transactions.

### Example

```ts
import {
  Configuration,
  WalletApi,
} from '@rugmunch/sdk';
import type { GetAnalysisApiV1WalletAddressAnalysisGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new WalletApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
  } satisfies GetAnalysisApiV1WalletAddressAnalysisGetRequest;

  try {
    const data = await api.getAnalysisApiV1WalletAddressAnalysisGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **address** | `string` |  | [Defaults to `undefined`] |
| **chain** | `string` |  | [Optional] [Defaults to `&#39;solana&#39;`] |

### Return type

[**WalletAnalysis**](WalletAnalysis.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getBalanceApiV1WalletAddressBalanceGet

> Balance getBalanceApiV1WalletAddressBalanceGet(address, chain)

Get Balance

Wallet balance + token holdings.

### Example

```ts
import {
  Configuration,
  WalletApi,
} from '@rugmunch/sdk';
import type { GetBalanceApiV1WalletAddressBalanceGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new WalletApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
  } satisfies GetBalanceApiV1WalletAddressBalanceGetRequest;

  try {
    const data = await api.getBalanceApiV1WalletAddressBalanceGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **address** | `string` |  | [Defaults to `undefined`] |
| **chain** | `string` |  | [Optional] [Defaults to `&#39;solana&#39;`] |

### Return type

[**Balance**](Balance.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getTransactionsApiV1WalletAddressTransactionsGet

> PaginatedResponse getTransactionsApiV1WalletAddressTransactionsGet(address, chain, limit)

Get Transactions

Recent transactions for a wallet.

### Example

```ts
import {
  Configuration,
  WalletApi,
} from '@rugmunch/sdk';
import type { GetTransactionsApiV1WalletAddressTransactionsGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new WalletApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
    // number (optional)
    limit: 56,
  } satisfies GetTransactionsApiV1WalletAddressTransactionsGetRequest;

  try {
    const data = await api.getTransactionsApiV1WalletAddressTransactionsGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **address** | `string` |  | [Defaults to `undefined`] |
| **chain** | `string` |  | [Optional] [Defaults to `&#39;solana&#39;`] |
| **limit** | `number` |  | [Optional] [Defaults to `50`] |

### Return type

[**PaginatedResponse**](PaginatedResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## scanApiV1WalletScanPost

> ScanResult scanApiV1WalletScanPost(appDomainWalletModelsScanRequest)

Scan

Multi-chain threat scan. Auth optional — freemium rate-limit applied in legacy router.

### Example

```ts
import {
  Configuration,
  WalletApi,
} from '@rugmunch/sdk';
import type { ScanApiV1WalletScanPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new WalletApi();

  const body = {
    // AppDomainWalletModelsScanRequest
    appDomainWalletModelsScanRequest: ...,
  } satisfies ScanApiV1WalletScanPostRequest;

  try {
    const data = await api.scanApiV1WalletScanPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **appDomainWalletModelsScanRequest** | [AppDomainWalletModelsScanRequest](AppDomainWalletModelsScanRequest.md) |  | |

### Return type

[**ScanResult**](ScanResult.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

