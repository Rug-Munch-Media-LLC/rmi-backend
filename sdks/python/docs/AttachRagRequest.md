# AttachRagRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**chain** | **str** |  | 
**address** | **str** |  | 
**qdrant_point_id** | **str** |  | 

## Example

```python
from rugmunch.models.attach_rag_request import AttachRagRequest

# TODO update the JSON string below
json = "{}"
# create an instance of AttachRagRequest from a JSON string
attach_rag_request_instance = AttachRagRequest.from_json(json)
# print the JSON string representation of the object
print(AttachRagRequest.to_json())

# convert the object into a dict
attach_rag_request_dict = attach_rag_request_instance.to_dict()
# create an instance of AttachRagRequest from a dict
attach_rag_request_from_dict = AttachRagRequest.from_dict(attach_rag_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


