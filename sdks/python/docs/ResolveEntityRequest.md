# ResolveEntityRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**wallet_id** | **str** |  | 
**max_chains** | **int** |  | [optional] [default to 5]

## Example

```python
from rugmunch.models.resolve_entity_request import ResolveEntityRequest

# TODO update the JSON string below
json = "{}"
# create an instance of ResolveEntityRequest from a JSON string
resolve_entity_request_instance = ResolveEntityRequest.from_json(json)
# print the JSON string representation of the object
print(ResolveEntityRequest.to_json())

# convert the object into a dict
resolve_entity_request_dict = resolve_entity_request_instance.to_dict()
# create an instance of ResolveEntityRequest from a dict
resolve_entity_request_from_dict = ResolveEntityRequest.from_dict(resolve_entity_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


