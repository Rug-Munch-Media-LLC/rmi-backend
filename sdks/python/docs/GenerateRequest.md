# GenerateRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**subject_type** | **str** |  | 
**subject_id** | **str** | \&quot;chain:address\&quot; | 
**model** | **str** |  | [optional] [default to 'deepseek-v3']
**save** | **bool** |  | [optional] [default to True]

## Example

```python
from rugmunch.models.generate_request import GenerateRequest

# TODO update the JSON string below
json = "{}"
# create an instance of GenerateRequest from a JSON string
generate_request_instance = GenerateRequest.from_json(json)
# print the JSON string representation of the object
print(GenerateRequest.to_json())

# convert the object into a dict
generate_request_dict = generate_request_instance.to_dict()
# create an instance of GenerateRequest from a dict
generate_request_from_dict = GenerateRequest.from_dict(generate_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


