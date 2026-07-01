# McpApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**directCallMcpCallToolIdPost**](McpApi.md#directcallmcpcalltoolidpost) | **POST** /mcp/call/{tool_id} | Direct Call |
| [**jsonrpcHandlerMcpPost**](McpApi.md#jsonrpchandlermcppost) | **POST** /mcp | Jsonrpc Handler |
| [**listToolsMcpToolsGet**](McpApi.md#listtoolsmcptoolsget) | **GET** /mcp/tools | List Tools |



## directCallMcpCallToolIdPost

> { [key: string]: any; } directCallMcpCallToolIdPost(toolId)

Direct Call

Direct tool execution (no JSON-RPC). For curl/scripts.

### Example

```ts
import {
  Configuration,
  McpApi,
} from '@rugmunch/sdk';
import type { DirectCallMcpCallToolIdPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new McpApi();

  const body = {
    // string
    toolId: toolId_example,
  } satisfies DirectCallMcpCallToolIdPostRequest;

  try {
    const data = await api.directCallMcpCallToolIdPost(body);
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
| **toolId** | `string` |  | [Defaults to `undefined`] |

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


## jsonrpcHandlerMcpPost

> { [key: string]: any; } jsonrpcHandlerMcpPost(jsonRpcRequest)

Jsonrpc Handler

JSON-RPC 2.0 endpoint for MCP clients.  Methods:   - initialize           → returns server info   - tools/list            → returns tool catalog   - tools/call            → dispatches to backend   - resources/list        → empty   - prompts/list          → empty

### Example

```ts
import {
  Configuration,
  McpApi,
} from '@rugmunch/sdk';
import type { JsonrpcHandlerMcpPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new McpApi();

  const body = {
    // JsonRpcRequest
    jsonRpcRequest: ...,
  } satisfies JsonrpcHandlerMcpPostRequest;

  try {
    const data = await api.jsonrpcHandlerMcpPost(body);
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
| **jsonRpcRequest** | [JsonRpcRequest](JsonRpcRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

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


## listToolsMcpToolsGet

> { [key: string]: any; } listToolsMcpToolsGet()

List Tools

Plain JSON endpoint (for direct integration, no JSON-RPC).

### Example

```ts
import {
  Configuration,
  McpApi,
} from '@rugmunch/sdk';
import type { ListToolsMcpToolsGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new McpApi();

  try {
    const data = await api.listToolsMcpToolsGet();
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

