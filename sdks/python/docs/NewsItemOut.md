# NewsItemOut


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**news_id** | **str** |  | [optional] 
**url** | **str** |  | [optional] 
**title** | **str** |  | [optional] 
**summary** | **str** |  | [optional] 
**source** | **str** |  | [optional] 
**published_at** | **datetime** |  | [optional] 
**chains_mentioned** | **List[str]** |  | [optional] 
**tokens_mentioned** | **List[str]** |  | [optional] 
**sentiment_score** | **float** |  | [optional] 
**score** | **float** |  | [optional] 

## Example

```python
from rugmunch.models.news_item_out import NewsItemOut

# TODO update the JSON string below
json = "{}"
# create an instance of NewsItemOut from a JSON string
news_item_out_instance = NewsItemOut.from_json(json)
# print the JSON string representation of the object
print(NewsItemOut.to_json())

# convert the object into a dict
news_item_out_dict = news_item_out_instance.to_dict()
# create an instance of NewsItemOut from a dict
news_item_out_from_dict = NewsItemOut.from_dict(news_item_out_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


