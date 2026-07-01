# TokenApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getDetailApiV1TokenAddressGet**](TokenApi.md#getdetailapiv1tokenaddressget) | **GET** /api/v1/token/{address} | Get Detail |
| [**getHoldersApiV1TokenAddressHoldersGet**](TokenApi.md#getholdersapiv1tokenaddressholdersget) | **GET** /api/v1/token/{address}/holders | Get Holders |
| [**getLiquidityApiV1TokenAddressLiquidityGet**](TokenApi.md#getliquidityapiv1tokenaddressliquidityget) | **GET** /api/v1/token/{address}/liquidity | Get Liquidity |
| [**getRiskApiV1TokenAddressRiskGet**](TokenApi.md#getriskapiv1tokenaddressriskget) | **GET** /api/v1/token/{address}/risk | Get Risk |
| [**scanApiV1TokenScanPost**](TokenApi.md#scanapiv1tokenscanpost) | **POST** /api/v1/token/scan | Scan |



## getDetailApiV1TokenAddressGet

> TokenDetail getDetailApiV1TokenAddressGet(address, chain)

Get Detail

Token metadata + supply + verification.

### Example

```ts
import {
  Configuration,
  TokenApi,
} from '@rugmunch/sdk';
import type { GetDetailApiV1TokenAddressGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new TokenApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
  } satisfies GetDetailApiV1TokenAddressGetRequest;

  try {
    const data = await api.getDetailApiV1TokenAddressGet(body);
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

[**TokenDetail**](TokenDetail.md)

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


## getHoldersApiV1TokenAddressHoldersGet

> PaginatedResponse getHoldersApiV1TokenAddressHoldersGet(address, chain, limit)

Get Holders

Top token holders.

### Example

```ts
import {
  Configuration,
  TokenApi,
} from '@rugmunch/sdk';
import type { GetHoldersApiV1TokenAddressHoldersGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new TokenApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
    // number (optional)
    limit: 56,
  } satisfies GetHoldersApiV1TokenAddressHoldersGetRequest;

  try {
    const data = await api.getHoldersApiV1TokenAddressHoldersGet(body);
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
| **limit** | `number` |  | [Optional] [Defaults to `100`] |

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


## getLiquidityApiV1TokenAddressLiquidityGet

> { [key: string]: any; } getLiquidityApiV1TokenAddressLiquidityGet(address, chain)

Get Liquidity

Token liquidity + pool info.

### Example

```ts
import {
  Configuration,
  TokenApi,
} from '@rugmunch/sdk';
import type { GetLiquidityApiV1TokenAddressLiquidityGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new TokenApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
  } satisfies GetLiquidityApiV1TokenAddressLiquidityGetRequest;

  try {
    const data = await api.getLiquidityApiV1TokenAddressLiquidityGet(body);
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

**{ [key: string]: any; }**

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


## getRiskApiV1TokenAddressRiskGet

> TokenRisk getRiskApiV1TokenAddressRiskGet(address, chain)

Get Risk

Combined risk: holders + liquidity + heuristics.

### Example

```ts
import {
  Configuration,
  TokenApi,
} from '@rugmunch/sdk';
import type { GetRiskApiV1TokenAddressRiskGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new TokenApi();

  const body = {
    // string
    address: address_example,
    // string (optional)
    chain: chain_example,
  } satisfies GetRiskApiV1TokenAddressRiskGetRequest;

  try {
    const data = await api.getRiskApiV1TokenAddressRiskGet(body);
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

[**TokenRisk**](TokenRisk.md)

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


## scanApiV1TokenScanPost

> TokenScanResult scanApiV1TokenScanPost(tokenScanRequest)

Scan

Full token scan. Multi-module. Freemium rate-limit applied in legacy router.

### Example

```ts
import {
  Configuration,
  TokenApi,
} from '@rugmunch/sdk';
import type { ScanApiV1TokenScanPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new TokenApi();

  const body = {
    // TokenScanRequest
    tokenScanRequest: ...,
  } satisfies ScanApiV1TokenScanPostRequest;

  try {
    const data = await api.scanApiV1TokenScanPost(body);
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
| **tokenScanRequest** | [TokenScanRequest](TokenScanRequest.md) |  | |

### Return type

[**TokenScanResult**](TokenScanResult.md)

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

