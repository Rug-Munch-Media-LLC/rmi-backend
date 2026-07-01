# NewsAnalysisResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**news_id** | **str** |  | 
**analysis** | **str** |  | 
**model** | **str** |  | [optional] 
**error** | **str** |  | [optional] 

## Example

```python
from rugmunch.models.news_analysis_response import NewsAnalysisResponse

# TODO update the JSON string below
json = "{}"
# create an instance of NewsAnalysisResponse from a JSON string
news_analysis_response_instance = NewsAnalysisResponse.from_json(json)
# print the JSON string representation of the object
print(NewsAnalysisResponse.to_json())

# convert the object into a dict
news_analysis_response_dict = news_analysis_response_instance.to_dict()
# create an instance of NewsAnalysisResponse from a dict
news_analysis_response_from_dict = NewsAnalysisResponse.from_dict(news_analysis_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


