# JsonRpcRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**jsonrpc** | **str** |  | [optional] [default to '2.0']
**method** | **str** |  | 
**params** | **Dict[str, object]** |  | [optional] 
**id** | [**Id**](Id.md) |  | [optional] 

## Example

```python
from rugmunch.models.json_rpc_request import JsonRpcRequest

# TODO update the JSON string below
json = "{}"
# create an instance of JsonRpcRequest from a JSON string
json_rpc_request_instance = JsonRpcRequest.from_json(json)
# print the JSON string representation of the object
print(JsonRpcRequest.to_json())

# convert the object into a dict
json_rpc_request_dict = json_rpc_request_instance.to_dict()
# create an instance of JsonRpcRequest from a dict
json_rpc_request_from_dict = JsonRpcRequest.from_dict(json_rpc_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


