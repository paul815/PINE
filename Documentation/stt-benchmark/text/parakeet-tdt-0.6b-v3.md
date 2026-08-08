# parakeet-tdt-0.6b-v3

| | |
|---|---|
| модель | `nvidia/parakeet-tdt-0.6b-v3` |
| время расшифровки | 64.03 с |
| пик VRAM | 10051 МБ |
| сегментов | 180 |
| пословных таймингов | 3093 |

---

**00:00**  Represent per page.

**00:02**  So, like each string represents whatever's on an entire page.

**00:07**  And then the last page that the user looked at, this would just be some integer, and this would be an index into this list of strings.

**00:14**  So I would have to remember that this, you know, there might be an off by one error here.

**00:23**  Um, because in Python we start at zero, but like naturally when we're reading a book, we might start at one.

**00:29**  Yep.

**00:31**  Now for this library, for this collection of books, okay.

**00:34**  Well, so for this active book, um I you could either use a Boolean where you set one book to active and the rest to f to inactive.

**00:45**  But I think that that would be kind of excessive because every single time we toggle a book as active, we have to toggle everything else as inactive, right?

**00:54**  You can't have two active books.

**00:56**  Yeah, yeah.

**00:58**  Okay.

**00:58**  Yeah, that makes sense.

**01:00**  Okay, so then I think in that case, instead, what I would rather do is have some sort of um a string or some sort of like an ID matching system where we have like one um variable that's set to the active book, and that might be the title or the ID of a certain book in the library.

**01:24**  So maybe, maybe we can just add ID here.

**01:28**  Um and maybe I can make that also a string or an integer or something.

**01:33**  Yeah.

**01:34**  And I'm gonna add a question mark to kind of just say, like, maybe we could implement that, but I'm not sure if we'd want to right now.

**01:41**  Um so this would correspond to ID.

**01:48**  So uh this would just be some variable.

**01:56**  And then this collection of books.

**01:59**  So I guess like when we display this page in an active book, um I actually so I think that this we this might have an advantage being like some sort of a lookup table because we have a certain sense of an ID, right?

**02:22**  And I think what could make sense here is having the ID correspond to the book object that we define up here.

**02:29**  So this could be a book object, right?

**02:32**  And so um we don't really need this ID.

**02:35**  I think that if all the titles are unique, then then we don't need this ID.

**02:40**  We can just we can just use the titles as the IDs.

**02:44**  Uh but in some cases, in the real world, not all titles are unique, and so that's when an ID might might come in handy.

**02:52**  Um and so I'll leave that like what are our assumptions here?

**02:58**  Can we assume that the titles are unique or should I be using this ID structure?

**03:02**  I think I like the ID structure because yeah, we're not necessarily gonna know that everything's unique, so I think this is a little bit more robust.

**03:10**  Okay, awesome.

**03:11**  So I think um this representation looks good to me.

**03:15**  So I'm just gonna go through uh the requirements again just to make sure that I've covered all my bases.

**03:21**  So here I want all books um okay, so I want a library of books that I can add to or remove from, and so here I have a library, and anytime I want to add to this library, I can just add an ID, I can add the book.

**03:35**  If I want to remove um from the from the library, then I can just simply delete the key item pair or the key whatever pair, key value pair.

**03:47**  Um I can set a book from their library as active, and so here I have this active book, and we could always say, like, if there is no active book, then we set this to none or something like that.

**04:00**  Um the reading application remembers where a user left off.

**04:04**  So we have that here, right?

**04:06**  The last page that the user looked at, and not just in the active book, and then um displaying one page of text at a time in the active book.

**04:15**  So we have the active book, we can get the book object from that, and then we can go to the page that the user last looked at by indexing this value into this list.

**04:28**  So I think we've covered all the bases here.

**04:31**  Cool.

**04:31**  Um let's do uh a couple things.

**04:35**  I guess I think as a starting point, uh and this doesn't have to get too crazy, but I would love to just see some you know Python pseudocode or even some Python kind of code just to like maybe flesh out one of these classes.

**04:46**  I just want to uh you know look at that a little bit and how you would actually start writing this code.

**04:52**  Yeah, sure.

**04:53**  So so for example, here I let's start with the book.

**04:57**  Um and when we define a class, let's initialize this.

**05:05**  So when we initialize a book, we definitely should pass in the title and then um the content, right?

**05:13**  I'm assuming that when we add a book, these things have to be given to us.

**05:17**  And then I'm assuming that when we add a book, we don't have a last page that the user has looked at.

**05:24**  Is that an okay assumption?

**05:26**  Yeah, I like that assumption.

**05:27**  Okay.

**05:29**  So we can set the title of this book just equal to whatever the title is.

**05:33**  Uh the content of this book.

**05:36**  And I'm going to assume that these are given to us in the formatting that we want.

**05:42**  So the title is a string and the content is a list of strings that correspond to the pages.

**05:46**  Um, and I'm gonna create a variable last page and I'm gonna set that equal to just zero.

**05:54**  I could also probably set it equal to negative one or something, but I think zero makes sense because that would be the beginning, right?

**06:00**  Yep.

**06:01**  And um, and then we need to come up with some sort of an ID.

**06:06**  So typically maybe we would have some sort of function that takes in the title and the content, or maybe the author or something, and like returns a unique ID.

**06:17**  Um, but I think maybe something that we can do here just very simply is uh have some sort of counter in the library, and then every single time we like add a new book, we can just uh associate that counter with that book, and so then we know that they're all unique and um and they all have their own IDs.

**06:41**  Would that make sense?

**06:42**  Yeah, that that makes sense.

**06:44**  Okay, so then I would have to pass in the ID.

**06:48**  Um yeah.

**06:50**  And so what I'm actually going to do is self.id equals ID.

**06:56**  Uh and then yeah, and so then uh for example, we might want like a display page, right, in this book.

**07:06**  And so for that, then we just want to return um whatever is at the last page of the content.

**07:18**  And I guess let's build on this a little bit.

**07:20**  Uh another useful function, you know, obviously you want to start at the the last page, but let's say we're starting to turn the page, what might that look like?

**07:29**  Right, and so if we're turning the page, then all we want to do is increment the last page, right?

**07:39**  And so we might do self dot last page and we just increment that by one.

**07:45**  Um and then we could call display page after that.

**07:50**  Uh if we assume that, like we could if if we assume that these two go together, so if we assume that when we turn the page, we want to display that page, then I would just return display page.

**08:05**  Yep.

**08:06**  Like that.

**08:08**  Um then actually I'm just gonna go ahead and code up this library as well.

**08:16**  So this library, we have this collection of books, and then also the active book.

**08:24**  So rather than uh initializing the library with books, I think let's just add them um to this.

**08:33**  So initially, this collection might just be an empty dictionary, and then the active book might be none, right?

**08:44**  So then um when we want to add stuff, so add to collection uh book what I'm going to do, so there's either the user could like or our API could like return a book with the uh with the title and the content, etc.

**09:16**  But since we're we said that we would pass the ID through like the library because we might have like a counter, like some sort of ID counter.

**09:24**  I mean, this is not the best way to do it, but I think this is like a fine hack for right now, um just to make all the IDs unique.

**09:33**  What we can do here is um we can also when we want to add a book, just pass in the title and the content.

**09:43**  And so then our new book is going to be a book, as we've defined above, with the ID and um the title and the count or the content.

**09:58**  And then after we create this new book, we want to increment the ID counter.

**10:06**  Uh, and then we also want to add this new book into the collection, right?

**10:10**  So I'm gonna add self dot collection, um, and I'm going to make the ID, whatever the ID of the book is.

**10:22**  So actually, I'm just gonna call new book.id because I think that's a bit cleaner.

**10:26**  And this is going to be the new book, and then we increment the counter by one.

**10:31**  So this is us adding to the collection.

**10:34**  Um, of course, we want to remove from the collection, and so we want we said that we wanted to remove based on the ID, so all we have to do is I think there's this delete in Python.

**10:53**  I'm not sure if there is a do you know, Keith?

**10:57**  Okay.

**10:59**  Is there a remove?

**11:02**  I think that there's, I mean, it it should autocomplete here in um the coder pad.

**11:06**  Oh, isn't it Dell?

**11:08**  Isn't there a Dell?

**11:10**  Um I mean I think the biggest thing is is it gonna be happening in place or is it going to be happening in creating a copy?

**11:17**  Um I I understand Yeah, so we want it in place from the collection.

**11:22**  I I understand what you're trying to do, so the exact details is is not super, super important to me.

**11:28**  Okay.

**11:29**  Yeah, so anyways, this is removing from the collection.

**11:32**  Um and when we set active book, again, we want to do this based on the ID.

**11:40**  So I'm going to make self dottivbook equal to whatever that ID is.

**11:47**  Uh and let's see.

**11:53**  So the user has a library of books they can add or remove from.

**11:57**  We did that.

**11:58**  Setting a book as active, okay, and then remembering where user left off.

**12:02**  So we've actually taken care of that already implicitly in our um book uh class.

**12:08**  And then the reading application only displays a page of text at a time.

**12:12**  Alright, so then here we can say define display page.

**12:18**  And actually, we don't even need the the ID of the book because we already have the active book ID.

**12:25**  Right?

**12:25**  And so what I'm going to do is I'm gonna go into my collection um and I'm gonna get the active book like this.

**12:35**  So yep.

**12:39**  Blah blah.

**12:40**  And I'm going to click or I'm gonna do dot display.

**12:44**  Okay.

**12:48**  And then of course, we also have this turn page that I could um this code up as well.

**12:58**  Yeah.

**13:00**  So you get the idea, but so we want to oops, turn that page.

**13:11**  Um, I do think that some like drawbacks here are that uh okay, so one thing that we could have done is instead of this active book, instead of saving the ID, we could have saved the book itself.

**13:26**  And I think from a pipe pythonic standpoint that might make sense, but then in the future, if we're using some sort of database or something, an ID would make more sense because that's easier to store in like a table.

**13:36**  Um and if uh and okay, also for this display page, you might see that we've called this display page and turn page up here in the book.

**13:48**  And the reason why I did that rather than um rather than just call like self dot book dot content last page, like book dot last page or something, was because I think that like for example, this should be independent of how we implement the book, right?

**14:08**  So we want to keep those as kind of block like separate blocks as possible.

**14:14**  And so here, what this allows me to do is when I go back to my book, like if I decide to scrap this implementation, implement something else.

**14:22**  I know that I would just have to implement the methods display page and turn page for that to work.

**14:28**  Um and like these functions should work.

**14:32**  This this looks good to me.

**14:33**  I like the start.

**14:34**  I think that this uh definitely lays out the foundation of what I was uh looking for with the uh requirements of this application.

**14:41**  So nice work with this.

**14:43**  Couple quick follow-up questions before we kind of branch into more of an algorithms uh type question as the follow-up here.

**14:52**  Um question is you know, let's say you have an older reader and their vision isn't as great, so they need to make this the size of the font bigger.

**15:02**  So kind of keeping that in mind that you might have this variable font size, how might you I guess refactor or change or modify your current structure?

**15:11**  And you don't have to actually write code here, let's just discuss this to account for um that increase in size.

**15:20**  Yeah.

**15:21**  Yeah, so I so when we increase something in size, like I'm just thinking about intuitively on our like phone screens or something, um, that type that tends to shift the content, right?

**15:32**  So like some page that might hold a hundred words or a hundred characters at you know um a small font size might only hold 50 if when we double that font size.

**15:44**  So uh for the book, instead of having this list, what I might do is I might just save the entire book as a string.

**15:55**  And um and based, so I would let me just edit this in here because I think it's easier to visualize what I'm saying.

**16:03**  But so I would might I might have like a font size equal to um I guess we could just use like the traditional like 12-point font or like whatever.

**16:16**  Uh but when we have this font size, we could come up with some sort of um characters per page calculation, right?

**16:27**  And so that would be based off the font size.

**16:29**  So calculate, so this is gonna be pseudocode now.

**16:32**  This calculate doesn't actually exist, but calculate uh based on that font size.

**16:40**  Um and I'm commenting it out so it doesn't have any errors, but then uh when we do calculate that, then what we can do in order to get our um in order to get this display page thing would be instead of indexing into this content, which is now just a long string of characters, what we can do here is we can return, um, we would have to do some sort of calculation, right?

**17:11**  So we would need to we have this characters per page, and we can multiply that by whatever the last page is, and um, so this would give us like kind of the starting index of this long string of characters to start uh the content of that page.

**17:34**  Start index would be this, and then what we would want to do is um what we would want to do is then return the self dot content from that start index until the start index plus the number of characters on that page.

**17:56**  So that would be our end index.

**17:58**  So actually, let me just write that out to make things a little bit more clear.

**18:07**  So now essentially what I've done is we have this variable font size, which we can calculate characters per page depending on that font size.

**18:16**  So that might be more characters or less characters depending on how big the font size is.

**18:21**  And then our content instead of a list of strings is now just a really long string of characters.

**18:26**  And I guess some assumptions that I'm making here are that uh this would just be approximate.

**18:32**  Um, so for example, if we had a backslash n, which is like a line break, right?

**18:38**  Um, that might just end up counting as a character or two characters.

**18:41**  But the assumption is that um that would be approximate per page, or maybe that we're using sort of some sort of like monospaced font or something like that.

**18:51**  Um but essentially now that we have the characters per page and then just a long string of characters that represents the content, we can index into that content depending on like based on the last page that we know.

**19:06**  And so what's really cool here is that when we do turn the page, uh we can just end up calling this.

**19:11**  So this is kind of that granularity that I was talking about.

**19:14**  Yeah, that that makes a lot of sense to me.

**19:16**  And definitely you see the advantage of having that function that you can change by having to implement something different.

**19:23**  Right.

**19:23**  Uh one last quick, I guess, follow-up, and then we'll we'll move on to the algorithms portion of this interview.

**19:29**  Um I'm just trying to think, you know, at a high level, let's say that we wanted we had multiple users in this system and they all share books.

**19:39**  So, like, you know, they might all share the Harry Potter books or something like that.

**19:42**  Like books are common among all of them.

**19:44**  How might you modify this?

**19:46**  Yeah.

**19:47**  Uh given that you know some of these qualities of a book might be shared between users because they're all trying to access that same book.

**19:54**  Does that make sense?

**19:56**  Yeah, so um, yeah, yeah, yeah, for sure.

**19:59**  So I
