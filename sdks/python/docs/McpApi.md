# rugmunch.McpApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**direct_call_mcp_call_tool_id_post**](McpApi.md#direct_call_mcp_call_tool_id_post) | **POST** /mcp/call/{tool_id} | Direct Call
[**jsonrpc_handler_mcp_post**](McpApi.md#jsonrpc_handler_mcp_post) | **POST** /mcp | Jsonrpc Handler
[**list_tools_mcp_tools_get**](McpApi.md#list_tools_mcp_tools_get) | **GET** /mcp/tools | List Tools


# **direct_call_mcp_call_tool_id_post**
> Dict[str, object] direct_call_mcp_call_tool_id_post(tool_id)

Direct Call

Direct tool execution (no JSON-RPC). For curl/scripts.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.McpApi(api_client)
    tool_id = 'tool_id_example' # str | 

    try:
        # Direct Call
        api_response = api_instance.direct_call_mcp_call_tool_id_post(tool_id)
        print("The response of McpApi->direct_call_mcp_call_tool_id_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling McpApi->direct_call_mcp_call_tool_id_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **tool_id** | **str**|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **jsonrpc_handler_mcp_post**
> Dict[str, object] jsonrpc_handler_mcp_post(json_rpc_request)

Jsonrpc Handler

JSON-RPC 2.0 endpoint for MCP clients.

Methods:
  - initialize           → returns server info
  - tools/list            → returns tool catalog
  - tools/call            → dispatches to backend
  - resources/list        → empty
  - prompts/list          → empty

### Example


```python
import rugmunch
from rugmunch.models.json_rpc_request import JsonRpcRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.McpApi(api_client)
    json_rpc_request = rugmunch.JsonRpcRequest() # JsonRpcRequest | 

    try:
        # Jsonrpc Handler
        api_response = api_instance.jsonrpc_handler_mcp_post(json_rpc_request)
        print("The response of McpApi->jsonrpc_handler_mcp_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling McpApi->jsonrpc_handler_mcp_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **json_rpc_request** | [**JsonRpcRequest**](JsonRpcRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **list_tools_mcp_tools_get**
> Dict[str, object] list_tools_mcp_tools_get()

List Tools

Plain JSON endpoint (for direct integration, no JSON-RPC).

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.McpApi(api_client)

    try:
        # List Tools
        api_response = api_instance.list_tools_mcp_tools_get()
        print("The response of McpApi->list_tools_mcp_tools_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling McpApi->list_tools_mcp_tools_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

