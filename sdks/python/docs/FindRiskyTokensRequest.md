# FindRiskyTokensRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**min_rug_count** | **int** |  | [optional] [default to 1]
**chain** | **str** |  | [optional] 
**limit** | **int** |  | [optional] [default to 50]

## Example

```python
from rugmunch.models.find_risky_tokens_request import FindRiskyTokensRequest

# TODO update the JSON string below
json = "{}"
# create an instance of FindRiskyTokensRequest from a JSON string
find_risky_tokens_request_instance = FindRiskyTokensRequest.from_json(json)
# print the JSON string representation of the object
print(FindRiskyTokensRequest.to_json())

# convert the object into a dict
find_risky_tokens_request_dict = find_risky_tokens_request_instance.to_dict()
# create an instance of FindRiskyTokensRequest from a dict
find_risky_tokens_request_from_dict = FindRiskyTokensRequest.from_dict(find_risky_tokens_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


