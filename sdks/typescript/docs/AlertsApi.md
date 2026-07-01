# AlertsApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**cancelApiV1AlertsAlertIdDelete**](AlertsApi.md#cancelapiv1alertsalertiddelete) | **DELETE** /api/v1/alerts/{alert_id} | Cancel |
| [**listAlertsApiV1AlertsGet**](AlertsApi.md#listalertsapiv1alertsget) | **GET** /api/v1/alerts | List Alerts |
| [**subscribeApiV1AlertsSubscribePost**](AlertsApi.md#subscribeapiv1alertssubscribepost) | **POST** /api/v1/alerts/subscribe | Subscribe |



## cancelApiV1AlertsAlertIdDelete

> cancelApiV1AlertsAlertIdDelete(alertId)

Cancel

Cancel (delete) a subscription. Owner only.

### Example

```ts
import {
  Configuration,
  AlertsApi,
} from '@rugmunch/sdk';
import type { CancelApiV1AlertsAlertIdDeleteRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AlertsApi();

  const body = {
    // string
    alertId: alertId_example,
  } satisfies CancelApiV1AlertsAlertIdDeleteRequest;

  try {
    const data = await api.cancelApiV1AlertsAlertIdDelete(body);
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
| **alertId** | `string` |  | [Defaults to `undefined`] |

### Return type

`void` (Empty response body)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **204** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## listAlertsApiV1AlertsGet

> PaginatedResponse listAlertsApiV1AlertsGet()

List Alerts

List alert subscriptions. Authenticated users see their own; anonymous sees all (legacy behavior).

### Example

```ts
import {
  Configuration,
  AlertsApi,
} from '@rugmunch/sdk';
import type { ListAlertsApiV1AlertsGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AlertsApi();

  try {
    const data = await api.listAlertsApiV1AlertsGet();
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

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## subscribeApiV1AlertsSubscribePost

> AlertSubscription subscribeApiV1AlertsSubscribePost(createAlertRequest)

Subscribe

Create a new alert subscription for a token.

### Example

```ts
import {
  Configuration,
  AlertsApi,
} from '@rugmunch/sdk';
import type { SubscribeApiV1AlertsSubscribePostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AlertsApi();

  const body = {
    // CreateAlertRequest
    createAlertRequest: ...,
  } satisfies SubscribeApiV1AlertsSubscribePostRequest;

  try {
    const data = await api.subscribeApiV1AlertsSubscribePost(body);
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
| **createAlertRequest** | [CreateAlertRequest](CreateAlertRequest.md) |  | |

### Return type

[**AlertSubscription**](AlertSubscription.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **201** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

