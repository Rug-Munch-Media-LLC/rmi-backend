# FeedbackRecord

Scanner → RAG feedback record.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**token_address** | **str** |  | 
**chain** | **str** |  | [optional] [default to 'solana']
**safety_score** | **float** |  | 
**risk_flags** | **List[str]** |  | [optional] 
**action** | **str** |  | [optional] [default to 'ingest']
**source** | **str** |  | [optional] [default to 'scanner']

## Example

```python
from rugmunch.models.feedback_record import FeedbackRecord

# TODO update the JSON string below
json = "{}"
# create an instance of FeedbackRecord from a JSON string
feedback_record_instance = FeedbackRecord.from_json(json)
# print the JSON string representation of the object
print(FeedbackRecord.to_json())

# convert the object into a dict
feedback_record_dict = feedback_record_instance.to_dict()
# create an instance of FeedbackRecord from a dict
feedback_record_from_dict = FeedbackRecord.from_dict(feedback_record_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


