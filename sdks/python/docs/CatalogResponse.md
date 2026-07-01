# CatalogResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**server** | **str** |  | [optional] [default to 'rugmunch-intelligence']
**version** | **str** |  | [optional] [default to '4.0']
**tools** | **List[Dict[str, object]]** |  | [optional] 
**payment_protocol** | **str** |  | [optional] [default to 'x402']

## Example

```python
from rugmunch.models.catalog_response import CatalogResponse

# TODO update the JSON string below
json = "{}"
# create an instance of CatalogResponse from a JSON string
catalog_response_instance = CatalogResponse.from_json(json)
# print the JSON string representation of the object
print(CatalogResponse.to_json())

# convert the object into a dict
catalog_response_dict = catalog_response_instance.to_dict()
# create an instance of CatalogResponse from a dict
catalog_response_from_dict = CatalogResponse.from_dict(catalog_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


