# X402Api

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**catalogApiV1X402CatalogGet**](X402Api.md#catalogapiv1x402catalogget) | **GET** /api/v1/x402/catalog | Catalog |
| [**receiptApiV1X402ReceiptsTxHashGet**](X402Api.md#receiptapiv1x402receiptstxhashget) | **GET** /api/v1/x402/receipts/{tx_hash} | Receipt |
| [**usageApiV1X402UsageGet**](X402Api.md#usageapiv1x402usageget) | **GET** /api/v1/x402/usage | Usage |
| [**verifyPaymentApiV1X402VerifyPost**](X402Api.md#verifypaymentapiv1x402verifypost) | **POST** /api/v1/x402/verify | Verify Payment |



## catalogApiV1X402CatalogGet

> CatalogResponse catalogApiV1X402CatalogGet()

Catalog

List all paid tools with pricing tiers.

### Example

```ts
import {
  Configuration,
  X402Api,
} from '@rugmunch/sdk';
import type { CatalogApiV1X402CatalogGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new X402Api();

  try {
    const data = await api.catalogApiV1X402CatalogGet();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**CatalogResponse**](CatalogResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## receiptApiV1X402ReceiptsTxHashGet

> ReceiptResponse receiptApiV1X402ReceiptsTxHashGet(txHash)

Receipt

Payment receipt for a specific on-chain tx.

### Example

```ts
import {
  Configuration,
  X402Api,
} from '@rugmunch/sdk';
import type { ReceiptApiV1X402ReceiptsTxHashGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new X402Api();

  const body = {
    // string
    txHash: txHash_example,
  } satisfies ReceiptApiV1X402ReceiptsTxHashGetRequest;

  try {
    const data = await api.receiptApiV1X402ReceiptsTxHashGet(body);
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
| **txHash** | `string` |  | [Defaults to `undefined`] |

### Return type

[**ReceiptResponse**](ReceiptResponse.md)

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


## usageApiV1X402UsageGet

> UsageResponse usageApiV1X402UsageGet()

Usage

Per-agent usage stats: calls made, $ spent, top tools.

### Example

```ts
import {
  Configuration,
  X402Api,
} from '@rugmunch/sdk';
import type { UsageApiV1X402UsageGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new X402Api();

  try {
    const data = await api.usageApiV1X402UsageGet();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**UsageResponse**](UsageResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## verifyPaymentApiV1X402VerifyPost

> { [key: string]: any; } verifyPaymentApiV1X402VerifyPost()

Verify Payment

Verify an X-Payment header for a given tool.

### Example

```ts
import {
  Configuration,
  X402Api,
} from '@rugmunch/sdk';
import type { VerifyPaymentApiV1X402VerifyPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new X402Api();

  try {
    const data = await api.verifyPaymentApiV1X402VerifyPost();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

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

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

