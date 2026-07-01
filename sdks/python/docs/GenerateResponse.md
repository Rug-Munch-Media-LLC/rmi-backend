# GenerateResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**report_id** | **str** |  | 
**subject_type** | **str** |  | 
**subject_id** | **str** |  | 
**risk_score** | **int** |  | 
**risk_tier** | **str** |  | 
**risk_factors** | **List[str]** |  | [optional] 
**generated_by_model** | **str** |  | 
**generated_at** | **str** |  | 
**sections** | **Dict[str, str]** |  | [optional] 
**markdown** | **str** |  | 
**paid_via_x402** | **str** |  | [optional] 
**error** | **str** |  | [optional] 

## Example

```python
from rugmunch.models.generate_response import GenerateResponse

# TODO update the JSON string below
json = "{}"
# create an instance of GenerateResponse from a JSON string
generate_response_instance = GenerateResponse.from_json(json)
# print the JSON string representation of the object
print(GenerateResponse.to_json())

# convert the object into a dict
generate_response_dict = generate_response_instance.to_dict()
# create an instance of GenerateResponse from a dict
generate_response_from_dict = GenerateResponse.from_dict(generate_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


